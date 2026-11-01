#!/usr/bin/env python3
"""pxcomfy.py: render images with ComfyUI's API, using the trained style LoRA.

  pxcomfy.py "<prompt>" --out DIR [--negative "..."] [--init IMG --denoise 0.5]
             [-n 4] [--seed N] [--steps 28] [--cfg 7] [--size 512]
             [--ckpt v1-5-pruned-emaonly.safetensors] [--lora mylora.safetensors]
             [--lora-strength 1.0] [--sampler euler_ancestral] [--scheduler normal]
             [--server http://127.0.0.1:8188] [--hires 2 --hires-denoise 0.35]

The workflow uses only ComfyUI's built-in nodes: checkpoint, LoRA, the two
prompts, an empty latent (from scratch) or the init image encoded (start
from a sprite), the sampler, decode, save. Each image is written to
DIR/comfy_s<seed>_sprite_.png, the name pxmake.sh's clean-up expects.

Only the Python standard library is needed.
"""

import argparse
import json
import mimetypes
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _post_json(server, path, data):
    req = urllib.request.Request(
        server + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit("error: ComfyUI refused the job: " + _comfy_reason(e)) from None


def _comfy_reason(e):
    """The readable part of a ComfyUI error answer (a bad file name, a node error)."""
    try:
        body = json.loads(e.read())
    except Exception:  # noqa: BLE001 - not JSON: show the HTTP status
        return f"HTTP {e.code}"
    parts = []
    err = body.get("error") or {}
    if isinstance(err, dict) and err.get("message"):
        parts.append(err["message"])
    for node in (body.get("node_errors") or {}).values():
        for ne in node.get("errors", []):
            parts.append(ne.get("details") or ne.get("message", ""))
    return "; ".join(p for p in parts if p) or f"HTTP {e.code}"


def check_files(a):
    """Stops early, with the fix, when the checkpoint or the LoRA isn't in ComfyUI."""
    try:
        info = _get_json(a.server, "/object_info/LoraLoader")["LoraLoader"]["input"]["required"]
    except Exception:  # noqa: BLE001 - an older ComfyUI: let the job itself report
        return None
    loras, _ckpts = info["lora_name"][0], info.get("ckpt_name", [None])[0]
    if a.lora not in loras:
        near = [n for n in loras if n.startswith(a.lora.rsplit(".", 1)[0].split("_")[0])]
        return (
            f"error: the LoRA {a.lora} isn't in ComfyUI's models/loras. Finish a training of this model, then on "
            "the Train page pick an epoch and click Use it (or Add it)."
            + (f" LoRA files with a close name: {', '.join(near[:6])}" if near else "")
        )
    try:
        ck = _get_json(a.server, "/object_info/CheckpointLoaderSimple")["CheckpointLoaderSimple"]["input"]["required"]
        if a.ckpt not in ck["ckpt_name"][0]:
            return f"error: the base model {a.ckpt} isn't in ComfyUI's models/checkpoints"
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_json(server, path):
    with urllib.request.urlopen(server + path) as r:
        return json.loads(r.read())


def upload(server, path):
    """Sends an image to ComfyUI's input folder; returns the name to load."""
    boundary = uuid.uuid4().hex
    name = "pxcomfy_" + uuid.uuid4().hex[:8] + "_" + os.path.basename(path)
    with open(path, "rb") as fh:
        content = fh.read()
    ctype = mimetypes.guess_type(path)[0] or "image/png"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        + content
        + (
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{boundary}--\r\n'
        ).encode()
    )
    req = urllib.request.Request(
        server + "/upload/image", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read())
    return res["name"] if not res.get("subfolder") else res["subfolder"] + "/" + res["name"]


def workflow(a, seed, init_name=None, mask_name=None):
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": a.ckpt}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": a.lora,
                "strength_model": a.lora_strength,
                "strength_clip": a.lora_strength,
            },
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": a.prompt, "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": a.negative, "clip": ["2", 1]}},
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0],
                "seed": seed,
                "steps": a.steps,
                "cfg": a.cfg,
                "sampler_name": a.sampler,
                "scheduler": a.scheduler,
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
                "denoise": a.denoise if init_name else 1.0,
            },
        },
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "pxcomfy"}},
    }
    if a.hires and a.hires > 1:
        # Second pass ("hires fix"): the first image scaled up with hard
        # edges, then lightly repainted at that size, so details land on a
        # finer grid (SD 1.5 draws ~8 px features at its own size).
        wf["10"] = {
            "class_type": "ImageScaleBy",
            "inputs": {"image": ["8", 0], "upscale_method": "nearest-exact", "scale_by": float(a.hires)},
        }
        wf["11"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["1", 2]}}
        wf["12"] = {
            "class_type": "KSampler",
            "inputs": {
                "model": ["2", 0],
                "seed": seed,
                "steps": a.steps,
                "cfg": a.cfg,
                "sampler_name": a.sampler,
                "scheduler": a.scheduler,
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["11", 0],
                "denoise": a.hires_denoise,
            },
        }
        wf["13"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["1", 2]}}
        wf["9"]["inputs"]["images"] = ["13", 0]
    if init_name:
        wf["6"] = {"class_type": "LoadImage", "inputs": {"image": init_name}}
        wf["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["1", 2]}}
        if mask_name:
            # touch-up: only the white part of the mask is repainted
            wf["14"] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
            wf["15"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["5", 0], "mask": ["14", 0]}}
            wf["7"]["inputs"]["latent_image"] = ["15", 0]
    else:
        wf["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": a.size, "height": a.size, "batch_size": 1}}
    return wf


def render(a, seed, init_name, mask_name=None):
    client = uuid.uuid4().hex
    res = _post_json(a.server, "/prompt", {"prompt": workflow(a, seed, init_name, mask_name), "client_id": client})
    if "prompt_id" not in res:
        raise RuntimeError(f"ComfyUI refused the workflow: {res}")
    pid = res["prompt_id"]
    t0 = time.time()
    while True:
        hist = _get_json(a.server, f"/history/{pid}")
        if pid in hist:
            entry = hist[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI error: {json.dumps(status.get('messages', ''))[:800]}")
            images = entry.get("outputs", {}).get("9", {}).get("images", [])
            if images:
                return images[0]
        if time.time() - t0 > a.timeout:
            raise TimeoutError(f"no result after {a.timeout} s")
        time.sleep(1.0)


def download(a, img, dst):
    q = urllib.parse.urlencode(
        {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
    )
    with urllib.request.urlopen(f"{a.server}/view?{q}") as r, open(dst, "wb") as fh:
        fh.write(r.read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--negative", default="blurry, 3d render, perspective, side view, text, watermark, soft shadow, gradient"
    )
    ap.add_argument("--init")
    ap.add_argument("--mask", help="with --init: repaint only the white part (same size as the init)")
    ap.add_argument("--denoise", type=float, default=0.6)
    ap.add_argument("-n", type=int, default=4)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--cfg", type=float, default=7.0)
    ap.add_argument(
        "--size",
        type=int,
        default=int(os.environ.get("PX_RES", "512")),
        help="render size: 512 for SD 1.5, 1024 for SDXL",
    )
    ap.add_argument("--ckpt", default=os.environ.get("PX_CKPT", "v1-5-pruned-emaonly.safetensors"))
    ap.add_argument(
        "--lora",
        default=os.environ.get("PX_LORA"),
        required=not os.environ.get("PX_LORA"),
        help="the LoRA file in ComfyUI's models/loras (default: $PX_LORA)",
    )
    ap.add_argument("--lora-strength", type=float, default=float(os.environ.get("PX_LORA_STRENGTH", "1.0")))
    ap.add_argument("--sampler", default="euler_ancestral")
    ap.add_argument("--scheduler", default="normal")
    ap.add_argument("--server", default=os.environ.get("PX_COMFY_URL", "http://127.0.0.1:8188"))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument(
        "--hires",
        type=float,
        default=float(os.environ.get("PX_HIRES", "1")),
        help="second pass at this scale (2 = 1024 px), for sprites bigger than 64 px",
    )
    ap.add_argument(
        "--hires-denoise",
        type=float,
        default=float(os.environ.get("PX_HIRES_DENOISE", "0.35")),
        help="how much the second pass repaints",
    )
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    try:
        _get_json(a.server, "/system_stats")
    except urllib.error.HTTPError:
        pass  # the server answered: it is running
    except (urllib.error.URLError, ConnectionError) as e:
        print(
            f"ComfyUI isn't running at {a.server} ({e}).\n"
            "Start it, e.g.: cd ~/ComfyUI && nohup .venv/bin/python main.py --listen 0.0.0.0 "
            "--port 8188 > ~/comfyui.log 2>&1 &   (or: systemctl start comfyui)",
            file=sys.stderr,
        )
        return 3
    problem = check_files(a)
    if problem:
        print(problem, file=sys.stderr)
        return 4
    init_name = upload(a.server, a.init) if a.init else None
    mask_name = upload(a.server, a.mask) if a.init and a.mask else None
    rng = random.Random()
    for i in range(a.n):
        seed = a.seed + i if a.seed is not None else rng.randrange(1, 2**31)
        t = time.time()
        img = render(a, seed, init_name, mask_name)
        dst = os.path.join(a.out, f"comfy_s{seed}_sprite_.png")
        download(a, img, dst)
        print(f"   [{i + 1}/{a.n}] seed={seed}  {time.time() - t:.0f} s  ->  {dst}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
