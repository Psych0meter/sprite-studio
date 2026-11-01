"""The page's palette arithmetic (webapp/static/palette.js) gives the same
pixels as pxtool.py, so the preview shows exactly what generation makes."""

import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

import pxtool
from conftest import ROOT, make_sprite

NODE = shutil.which("node")
RUNNER = """
const p = require(process.argv[1]);
let input = "";
process.stdin.on("data", (d) => { input += d; });
process.stdin.on("end", () => {
  const { w, data, pal } = JSON.parse(input);
  const px = Uint8ClampedArray.from(data);
  p.applyPalette(px, pal, w);
  process.stdout.write(JSON.stringify(Array.from(px)));
});
"""

PALETTES = [
    {"colours": [], "mode": "tune", "tune": {"brightness": 35, "saturation": 15}},
    {"colours": [], "mode": "tune", "tune": {"light": 60, "hue": 30, "contrast": -20}},
    {"colours": [], "mode": "tune", "tune": {"light": -40, "hue": -45, "saturation": -30, "brightness": -20}},
    {"colours": ["#201010", "#803030", "#e06060", "#ffd0d0"], "mode": "snap", "tune": {"brightness": 10}},
    {"colours": ["#202020", "#606060", "#a0a0a0", "#f0f0f0"], "weights": [4, 3, 2, 1], "mode": "match", "tune": {}},
]


@pytest.mark.skipif(NODE is None, reason="needs Node.js")
@pytest.mark.parametrize("pal", PALETTES)
def test_preview_equals_output(tmp_path, pal):
    src = make_sprite(tmp_path / "s.png", size=40, lit_from_below=True)
    a = np.array(Image.open(src).convert("RGBA"))
    a[..., 3] = np.where(a[..., 3] > 127, 255, 0)
    want = pxtool.apply_palette(a, pal)
    out = subprocess.run(
        [NODE, "-e", RUNNER, str(ROOT / "webapp" / "static" / "palette.js")],
        input=json.dumps({"w": a.shape[1], "data": a.reshape(-1).tolist(), "pal": pal}),
        capture_output=True,
        text=True,
        check=True,
    )
    got = np.array(json.loads(out.stdout), np.uint8).reshape(a.shape)
    assert (got == want).all()
