"""pxpreviews.sh with a stand-in for pxcomfy.py and a ComfyUI that only answers /system_stats."""

import http.server
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from test_pxmodel import new_model

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
STUB = """#!/usr/bin/env python3
import argparse, os, sys
ap = argparse.ArgumentParser()
ap.add_argument("prompt"); ap.add_argument("--out"); ap.add_argument("--negative"); ap.add_argument("-n")
ap.add_argument("--seed"); ap.add_argument("--steps"); ap.add_argument("--cfg")
a = ap.parse_args()
lora = os.path.join(os.environ["COMFY"], "models", "loras", os.environ["PX_LORA"])
if not os.path.exists(lora) or "fail" in a.prompt:
    sys.exit("error: stand-in refuses")
from PIL import Image
Image.new("RGB", (8, 8), (0, 255, 0)).save(os.path.join(a.out, f"comfy_s{a.seed}_sprite_.png"))
open(os.path.join(a.out, f"comfy_s{a.seed}_sprite_.json"), "w").write('{"seconds": 12.5}')
print("ok", os.environ["PX_LORA"], os.environ.get("PX_RES"), os.environ.get("PX_CKPT"), a.seed)
"""


@pytest.fixture
def comfy_url():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def run(tmp_path, env, url, **extra):
    scripts = tmp_path / "scripts"
    if not scripts.exists():
        shutil.copytree(SCRIPTS, scripts)
        (scripts / "pxcomfy.py").write_text(STUB)
    e = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",  # python3 with Pillow, as the tests
        "HOME": str(tmp_path),
        "PX_LORA_HOME": str(env["lora"]),
        "PX_INPUT": str(env["input"]),
        "PX_GAME": str(env["game"]),
        "COMFY": str(env["comfy"]),
        "PX_COMFY_URL": url,
        "CATEGORY": "units",
        "MODEL": "sdxl",
        **extra,
    }
    return subprocess.run(["bash", str(scripts / "pxpreviews.sh")], env=e, capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_renders_the_missing_previews_then_has_nothing_left(pxmodel, env, tmp_path, comfy_url):
    new_model(pxmodel, previews=["pxunit, red unit", "pxunit, blue unit"])
    out = env["lora"] / "units" / "output_xl"
    (out / "sample").mkdir(parents=True)
    for n in ("pxunit_xl-000002", "pxunit_xl-000004"):
        (out / f"{n}.safetensors").write_bytes(b"x")
    (out / "sample" / "pxunit_xl_e000002_00_20260101000000.png").write_bytes(b"x")  # exists: not rendered again
    r = run(tmp_path, env, comfy_url, PROMPTS="2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "=== [3/3]" in r.stdout
    names = sorted(p.name[:21] for p in (out / "sample").glob("*.png"))
    assert names == ["pxunit_xl_e000002_00_", "pxunit_xl_e000002_01_", "pxunit_xl_e000004_00_", "pxunit_xl_e000004_01_"]
    assert len(list((out / "sample").glob("*.json"))) == 3  # a render time for each new preview
    assert not list((env["comfy"] / "models" / "loras").glob("px_preview_*"))  # the temporary copies are gone
    again = run(tmp_path, env, comfy_url, PROMPTS="2")
    assert again.returncode == 0 and "nothing to render" in again.stdout


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_some_failures_exit_4_and_all_failures_exit_1(pxmodel, env, tmp_path, comfy_url):
    new_model(pxmodel, previews=["pxunit, red unit", "pxunit, fail please"])
    out = env["lora"] / "units" / "output_xl"
    out.mkdir(parents=True)
    (out / "pxunit_xl-000002.safetensors").write_bytes(b"x")
    assert run(tmp_path, env, comfy_url, PROMPTS="2").returncode == 4
    shutil.rmtree(out / "sample")
    new_model(pxmodel, previews=["pxunit, fail a", "pxunit, fail b"])
    assert run(tmp_path, env, comfy_url, PROMPTS="2").returncode == 1


def test_stops_with_a_hint_when_comfyui_does_not_answer(pxmodel, env, tmp_path):
    new_model(pxmodel, previews=["pxunit, red unit"])
    out = env["lora"] / "units" / "output_xl"
    out.mkdir(parents=True)
    (out / "pxunit_xl-000002.safetensors").write_bytes(b"x")
    r = run(tmp_path, env, "http://127.0.0.1:9")
    assert r.returncode == 2 and "isn't answering" in r.stderr
