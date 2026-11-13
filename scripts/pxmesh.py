#!/usr/bin/env python3
"""pxmesh: a top-down sprite seen from a tilted camera (a "3/4 view").

  pxmesh.py render  <sprite> <out.png> [--size 64] [--tilt 45] [--yaw 0] [--thickness 0.12]
                    [--detail 0.15] [--palette P] [--max-colours 64] [--raw RAW.png]
  pxmesh.py dataset <folder> <out-folder> [--yaws 0,90,180,270] [same options]

The sprite is read as a relief: its silhouette gives the outline, the distance
from the edge gives a rounded hull, and the light of its pixels adds a little
surface detail. The relief is drawn by a small z-buffer from a camera tilted
`--tilt` degrees from straight above (0: the original top-down view, 90: from
the side), after turning the ship `--yaw` degrees. The top keeps the sprite's
own colours, the sides are the same colours, darker. The render is then read
back into a sprite by `pxtool.post` (pixel grid, at most `--max-colours`
colours, outline, optional palette), so the result follows the same sprite
standard as what the generator makes.

`dataset` renders every sprite of a folder at each yaw. The files are named
`<sprite>_yaw<n>.png`; import them into a new model to train a LoRA for the
3/4 view (the original LoRA is untouched).

Not a generator: the far side and the underside are never drawn, the relief
is a guess from one view. Fine parts that stick out (antennas, guns) stay
flat. Needs numpy, pillow, scipy.
"""

import argparse
import json
import math
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pxtool  # noqa: E402

CANVAS = 512
SIDE_SHADE = 0.6  # how dark the sides of the hull are against the top


def relief(rgba, thickness=0.12, detail=0.15):
    """Height of every pixel, in sprite pixels: a rounded hull from the
    distance to the silhouette's edge, plus the sprite's own light as detail.
    thickness: the hull's height as a share of the sprite's size, detail: the
    light's share of that height."""
    a = rgba[..., 3] >= 128
    h, w = a.shape
    top = max(1.0, thickness * max(h, w))
    dt = ndimage.distance_transform_edt(a)
    radius = max(2.0, 0.25 * max(h, w))
    t = np.clip(dt / radius, 0, 1)
    z = top * np.sqrt(1 - (1 - t) ** 2)  # a circular profile: steep at the edge, flat on top
    if detail and a.any():
        rgb = rgba[..., :3].astype(float)
        lum = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]) / 255
        z = z + detail * top * (lum - lum[a].mean()) * 2
    return np.where(a, np.maximum(z, 0.5), 0.0)


def turn(rgba, z, yaw):
    """The sprite and its relief turned `yaw` degrees (clockwise, seen from above)."""
    yaw %= 360
    if yaw == 0:
        return rgba, z
    if yaw % 90 == 0:
        k = int(-(yaw // 90) % 4)
        return np.rot90(rgba, k).copy(), np.rot90(z, k).copy()
    out = ndimage.rotate(rgba, -yaw, axes=(1, 0), reshape=False, order=0)
    return out, ndimage.rotate(z, -yaw, axes=(1, 0), reshape=False, order=1)


def draw(rgba, z, k, tilt):
    """The relief seen from `tilt` degrees: an RGBA buffer (size * k wide and high), each sprite pixel k pixels."""
    th = math.radians(min(max(tilt, 0.0), 85.0))
    sin, cos = math.sin(th), math.cos(th)
    h, w = z.shape
    big_rgba = np.repeat(np.repeat(rgba, k, axis=0), k, axis=1)
    big_z = ndimage.zoom(z, k, order=1) * k
    big_z = np.where(big_rgba[..., 3] >= 128, big_z, 0.0)
    ys, xs = np.nonzero(big_rgba[..., 3] >= 128)
    zz = big_z[ys, xs]
    colour = big_rgba[ys, xs, :3].astype(float)
    side = (colour * SIDE_SHADE).astype(np.uint8)
    colour = colour.astype(np.uint8)
    height, width = h * k, w * k
    # centre the picture on the buffer, on a whole number of sprite pixels so the grid still fits
    top, bottom = -zz.max() * sin, (height - 1) * cos
    off = int(round((height - (bottom - top)) / 2 / k)) * k - top
    out = np.zeros((height, width, 4), np.uint8)
    zbuf = np.full((height, width), -np.inf)
    # one pass per screen row below a texel's top: row 0 is its top, the rest its side down to the ground
    reach = int(math.ceil(zz.max() * sin)) + 1
    for j in range(reach):
        zj = zz - j / sin if sin > 1e-6 else zz
        live = zj >= 0 if j else np.ones(len(zz), bool)
        if not live.any():
            break
        row = np.rint(ys[live] * cos - zz[live] * sin + off).astype(int) + j
        col = xs[live]
        depth = ys[live] * sin + zj[live] * cos
        ok = (row >= 0) & (row < height)
        row, col, depth, idx = row[ok], col[ok], depth[ok], np.nonzero(live)[0][ok]
        order = np.argsort(depth, kind="stable")  # far first: the nearest texel's write comes last
        row, col, depth, idx = row[order], col[order], depth[order], idx[order]
        win = depth > zbuf[row, col]
        row, col, depth, idx = row[win], col[win], depth[win], idx[win]
        zbuf[row, col] = depth
        out[row, col, :3] = colour[idx] if j == 0 else side[idx]
        out[row, col, 3] = 255
    return out


def render(src, dst, size=64, tilt=45.0, yaw=0.0, thickness=0.12, detail=0.15, raw=None, **post_args):
    """A top-down sprite as a sprite seen from `tilt` degrees, turned `yaw` degrees. The pixel grid is the
    one the generator uses (512 // size render pixels per sprite pixel); post_args go to pxtool.post."""
    dst = str(dst)
    im = pxtool.fit_sprite(Image.open(src).convert("RGBA"), size)
    rgba = np.array(im)
    z = relief(rgba, thickness, detail)
    rgba, z = turn(rgba, z, yaw)
    k = pxtool.pixel_scale(size, CANVAS)
    buf = draw(rgba, z, k, tilt)
    g = pxtool.grid_for(size, size, CANVAS)
    canvas = Image.new("RGB", (CANVAS, CANVAS), pxtool.hex_to_rgb(pxtool.KEY))
    pic = Image.fromarray(buf, "RGBA")
    canvas.paste(pic, (g["ox"], g["oy"]), pic)
    stem = os.path.splitext(dst)[0]
    raw = raw or stem + "_raw.png"
    canvas.save(raw)
    with open(raw + ".json", "w") as fh:
        json.dump(g, fh)
    pxtool.post(raw, dst, size=size, grid=raw + ".json", **post_args)
    return dst


def dataset(folder, out, yaws=(0, 90, 180, 270), **args):
    """Every sprite of `folder` rendered at each yaw into `out`; returns the files written."""
    os.makedirs(out, exist_ok=True)
    raw = os.path.join(out, ".raw")
    os.makedirs(raw, exist_ok=True)
    made = []
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".png":
            continue
        for yaw in yaws:
            dst = os.path.join(out, f"{stem}_yaw{int(yaw)}.png")
            render(os.path.join(folder, name), dst, yaw=yaw, raw=os.path.join(raw, f"{stem}_yaw{int(yaw)}.png"), **args)
            made.append(dst)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--size", type=int, default=64, help="size of the result (the sprite is fitted to it first)")
        p.add_argument("--tilt", type=float, default=45.0, help="camera angle from straight above, 0 to 85")
        p.add_argument("--thickness", type=float, default=0.12, help="height of the hull, as a share of the size")
        p.add_argument("--detail", type=float, default=0.15, help="how much the sprite's light bumps the surface")
        p.add_argument("--max-colours", type=int, default=64)
        p.add_argument("--palette", default=None, help="a palette file applied to the result")
        p.add_argument("--no-outline", action="store_true")

    r = sub.add_parser("render", help="one sprite")
    r.add_argument("src")
    r.add_argument("dst")
    r.add_argument("--yaw", type=float, default=0.0, help="turn the ship, degrees clockwise seen from above")
    r.add_argument("--raw", default=None, help="where to keep the render before the clean-up")
    common(r)
    d = sub.add_parser("dataset", help="every sprite of a folder at several yaws")
    d.add_argument("src")
    d.add_argument("dst")
    d.add_argument("--yaws", default="0,90,180,270", help="comma-separated degrees")
    common(d)
    a = ap.parse_args()
    args = {
        "size": a.size,
        "tilt": a.tilt,
        "thickness": a.thickness,
        "detail": a.detail,
        "colours": a.max_colours,
        "palette": a.palette,
        "outline": not a.no_outline,
    }
    if a.cmd == "render":
        render(a.src, a.dst, yaw=a.yaw, raw=a.raw, **args)
        print(f"{a.dst}: tilt {a.tilt}, yaw {a.yaw}")
    else:
        made = dataset(a.src, a.dst, yaws=[float(y) for y in a.yaws.split(",") if y.strip()], **args)
        print(f"{a.dst}: {len(made)} sprites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
