"""Shared fixtures: the scripts on the import path, synthetic sprites, and
an isolated environment (models, results, app state in a temporary folder)."""

import importlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "webapp"))


def make_sprite(path, size=32, colour=(160, 60, 60), lit_from_below=False, box=None):
    """A sprite: a filled rectangle with a vertical shading ramp (brighter at
    the top, or at the bottom when lit_from_below) and a bright core."""
    a = np.zeros((size, size, 4), np.uint8)
    y0, y1, x0, x1 = box or (size // 4, size - size // 4, size // 4, size - size // 4)
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0 - 1)
        f = 0.4 + 0.6 * (t if lit_from_below else 1 - t)
        a[y, x0:x1, :3] = [int(c * f) for c in colour]
        a[y, x0:x1, 3] = 255
    cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
    a[cy - 1 : cy + 1, cx - 1 : cx + 1, :3] = (255, 40, 40)
    Image.fromarray(a, "RGBA").save(path)
    return path


@pytest.fixture
def sprite(tmp_path):
    return make_sprite(tmp_path / "unit_red_1.png")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Empty data, models and state folders, pointed to by the environment."""
    dirs = {k: tmp_path / k for k in ("lora", "input", "game", "output", "state", "comfy")}
    for d in dirs.values():
        d.mkdir()
    (dirs["comfy"] / "models" / "loras").mkdir(parents=True)
    monkeypatch.setenv("PX_LORA_HOME", str(dirs["lora"]))
    monkeypatch.setenv("PX_INPUT", str(dirs["input"]))
    monkeypatch.setenv("PX_GAME", str(dirs["game"]))
    monkeypatch.setenv("PX_OUTPUT", str(dirs["output"]))
    monkeypatch.setenv("SG_WEB_STATE", str(dirs["state"]))
    monkeypatch.setenv("COMFY", str(dirs["comfy"]))
    monkeypatch.setenv("PX_COMFY_URL", "http://127.0.0.1:9")  # nothing listens there
    return dirs


@pytest.fixture
def pxmodel(env):
    import pxmodel as m

    m.HOME = os.environ["PX_LORA_HOME"]
    m.ROOTS.clear()
    m.ROOTS.update({"input": os.environ["PX_INPUT"], "game": os.environ["PX_GAME"]})
    return m


ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def web(env):
    """The web app on the isolated folders (imported afresh for each test), with nobody signed in."""
    from fastapi.testclient import TestClient

    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    c = TestClient(app.app)
    c.module = app
    return c


@pytest.fixture
def client(web):
    """The web app with an administrator signed in (accounts: tests/test_accounts.py)."""
    web.module.ACCOUNTS.create("admin", ADMIN_PASSWORD, role="admin", must_change=False)
    r = web.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return web
