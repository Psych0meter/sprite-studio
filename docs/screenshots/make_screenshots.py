#!/usr/bin/env python3
"""Rebuild the README screenshots (docs/screenshots/*.png).

Starts the web app on a temporary, synthetic data set (a "units" model whose
sprites are drawn by this script, a palette, one batch of fake results; no GPU,
no ComfyUI) and photographs its pages with Playwright and Chromium.

Usage: python3 docs/screenshots/make_screenshots.py
Needs: pip install -e ".[dev]" playwright (and its Chromium).
"""

import colorsys
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = Path(__file__).resolve().parent
PORT = 8197
VIEW = {"width": 1360, "height": 860}

GROUPS = [("red", 0.0), ("blue", 0.6), ("green", 0.33)]
KINDS = [("small", 32), ("medium", 64), ("large", 128)]
PALETTE = ["#1b1020", "#4a1f2e", "#7c2d3b", "#b84a4a", "#e0795a", "#f2b27a", "#fbe3b0", "#2e3a5c", "#5a78a8", "#9cc4e0"]


def draw_unit(size: int, hue: float, seed: int, detail: int = 1) -> Image.Image:
    """A symmetric top-down ship, nose up, lit from the top, on transparency."""
    rng = random.Random(seed)
    n = size * detail
    half = n // 2
    mask = np.zeros((n, half), bool)
    wing = rng.uniform(0.7, 0.95)
    start = rng.uniform(0.38, 0.5)
    for y in range(n):
        t = y / n
        body = half * (0.1 + 0.22 * np.sin(np.pi * min(1.0, t * 0.9 + 0.1)))
        span = body
        if t > start:
            u = (t - start) / (1 - start)
            span = max(body, half * wing * min(1.0, u * 2.2) * (1 - 0.55 * max(0.0, u - 0.6) / 0.4))
        mask[y, : max(1, int(span))] = True
    mask = mask[:, ::-1]
    full = np.concatenate([mask, mask[:, ::-1]], axis=1)
    img = np.zeros((n, n, 4), np.uint8)
    ys = np.arange(n)[:, None] / n
    xs = np.abs(np.arange(n)[None, :] - (n - 1) / 2) / half
    light = np.clip(1.05 - 0.6 * ys - 0.3 * xs, 0.15, 1)
    sat = 0.55 + 0.25 * np.cos(xs * 3)
    sat = np.broadcast_to(sat, (n, n))
    light = np.broadcast_to(light, (n, n))
    rgb = np.zeros((n, n, 3))
    for y in range(n):
        for x in range(n):
            rgb[y, x] = colorsys.hsv_to_rgb(hue, sat[y, x], light[y, x])
    img[..., :3] = np.clip(rgb * 255, 0, 255)
    img[..., 3] = np.where(full, 255, 0)
    cy, cx = int(n * 0.38), n // 2
    r = max(1, n // 14)
    img[cy - r : cy + r, cx - r : cx + r, :3] = (250, 230, 120)
    edge = full & ~np.pad(full, 1)[2:, 1:-1] | full & ~np.pad(full, 1)[:-2, 1:-1]
    edge |= full & ~np.pad(full, 1)[1:-1, 2:] | full & ~np.pad(full, 1)[1:-1, :-2]
    img[edge, :3] = (20, 14, 30)
    return Image.fromarray(img, "RGBA")


def build(tmp: Path) -> dict:
    d = {k: tmp / k for k in ("lora", "output", "state", "comfy", "input", "game")}
    for p in d.values():
        p.mkdir()
    (d["comfy"] / "models" / "loras").mkdir(parents=True)
    sys.path.insert(0, str(ROOT / "scripts"))
    os.environ["PX_LORA_HOME"] = str(d["lora"])
    import pxmodel

    m = pxmodel.from_template("generic", "topdown_units", "units", "Demo project")
    m["label"] = "Units"
    m["groups"] = [
        {"id": g, "label": g.title(), "words": f"{g} faction", "pattern": f"^{g}_", "enabled": True} for g, _ in GROUPS
    ]
    for k in m["kinds"]:
        k["pattern"] = rf"_{k['id']}_"
    pxmodel.save(m)
    sprites = Path(pxmodel.sprites_dir("units"))
    sprites.mkdir(parents=True, exist_ok=True)
    names = []
    for gi, (g, hue) in enumerate(GROUPS):
        for ki, (k, size) in enumerate(KINDS):
            for n in (1, 2, 3):
                name = f"{g}_{k}_{n}"
                draw_unit(size, hue + 0.015 * n, gi * 100 + ki * 10 + n).save(sprites / f"{name}.png")
                names.append((g, k, size, name, hue + 0.015 * n))
    (d["comfy"] / "models" / "loras" / "pxunit.safetensors").write_bytes(b"demo")  # a stand-in: no warning
    pal = d["lora"] / ".palettes"
    pal.mkdir()
    (pal / "warm.json").write_text(
        json.dumps(
            {
                "id": "warm",
                "label": "Warm metal",
                "colours": PALETTE,
                "tune": {"hue": 0, "saturation": 0, "brightness": 10, "contrast": 5, "light": 20},
                "mode": "tune",
                "words": True,
                "source": "demo",
            }
        )
    )
    # fake batch results: the same units with another hue and more detail
    batch = d["output"] / "batch-demo"
    for g, k, size, name, hue in names:
        if g != "red" or k == "large":
            continue
        f = batch / k / name
        f.mkdir(parents=True)
        draw_unit(size, hue, len(name)).save(f / "orig.png")
        for s in (1, 2, 3):
            draw_unit(size, hue + 0.04 * s, 7 * s + len(name)).save(f / f"{name}_s{s}.png")
        (f / "job.txt").write_text(
            f"name={name} model=units group={g} kind={k} lora=units.safetensors res=512 size={size} init=x palette=warm\n"
            f"prompt: pxunit, {g} faction, {k} unit, top-down view, facing up, pixel art, flat green background\n"
        )
        (f / ".done").touch()
    return d


def main() -> None:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as t:
        d = build(Path(t))
        env = dict(
            os.environ,
            PX_LORA_HOME=str(d["lora"]),
            SG_WEB_STATE=str(d["state"]),
            COMFY=str(d["comfy"]),
            PX_INPUT=str(d["input"]),
            PX_GAME=str(d["game"]),
            PX_OUTPUT=str(d["output"]),
            PX_COMFY_URL="http://127.0.0.1:9",
            SG_WEB_HOST="127.0.0.1",
            SG_WEB_PORT=str(PORT),
        )
        srv = subprocess.Popen([sys.executable, str(ROOT / "webapp" / "app.py")], env=env, cwd=ROOT / "webapp")
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=1)
                    break
                except OSError:
                    time.sleep(0.5)
            pages = {
                "models": "#/models/units",
                "palettes": "#/palettes",
                "generate": "#/generate",
                "results": "#/results",
            }
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page(viewport=VIEW, device_scale_factor=1)
                for name, route in pages.items():
                    pg.goto(f"http://127.0.0.1:{PORT}/{route}")
                    pg.wait_for_timeout(1200)
                    pg.screenshot(path=str(OUT / f"{name}.png"))
                    print("wrote", name)
                b.close()
        finally:
            srv.terminate()
            srv.wait(10)


if __name__ == "__main__":
    main()
