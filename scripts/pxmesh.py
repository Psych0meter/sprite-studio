#!/usr/bin/env python3
"""pxmesh: a top-down sprite seen from a tilted camera (a "3/4 view").

  pxmesh.py render  <sprite> <out.png> [--size 64] [--tilt 45] [--yaw 0] [--thickness 0.12]
                    [--detail 0.15] [--palette P] [--max-colours 64] [--raw RAW.png]
  pxmesh.py dataset <folder> <out-folder> [--yaws 0,90,180,270] [same options]
  pxmesh.py mesh    <sprite> <out.glb> [--size 128] [--triangles 5000] [--thickness 0.12] [--detail 0.15]
                    [--base 0.03] [--scale S] [--unlit]

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

`mesh` makes a closed low-poly 3D model instead (a binary glTF, `.glb`, which Godot, Blender and most engines
open): the same relief, its outline simplified and its top triangulated down to about `--triangles` triangles
in all (top, flat bottom and walls; any number from 100 to 200000, the upper limit set by the environment variable
`PX_MESH_MAX_TRIANGLES`; a sprite has only so many pixels, so a small one stops short of a very large number: give
`--size` a larger value to fit it to a finer grid), textured with the sprite itself, nearest-neighbour filtered, so the palette
stays exact. Flat shaded, +y up, the ship's nose toward -z, about one unit long (`--scale` units per sprite
pixel). `--unlit` marks the material unlit (`KHR_materials_unlit`) for a flat pixel-art look.

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
MIN_TRIANGLES = 100
# The most triangles one model may ask for. The default is far above what a game wants for one ship and far below what
# a card cannot draw; set PX_MESH_MAX_TRIANGLES for your own card and scene.
MAX_TRIANGLES = int(os.environ.get("PX_MESH_MAX_TRIANGLES", "200000"))
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


# --- 3D mesh -------------------------------------------------------------------------------------------------------


def check_triangles(n):
    """`n` as a whole number of triangles, or a ValueError saying the allowed range."""
    n = int(n)
    if not MIN_TRIANGLES <= n <= MAX_TRIANGLES:
        raise ValueError(
            f"triangles must be between {MIN_TRIANGLES} and {MAX_TRIANGLES} (got {n}); "
            "PX_MESH_MAX_TRIANGLES changes the upper limit"
        )
    return n


def _loops(mask):
    """The outlines of a mask as closed loops of pixel corners (x, y), found by following the pixel edges."""
    m = np.pad(mask, 1)
    inner = m[1:-1, 1:-1]
    edges = {}
    sides = (  # the neighbour to test, and the edge's two corners, going round each pixel clockwise
        (m[:-2, 1:-1], (0, 0), (1, 0)),
        (m[1:-1, 2:], (1, 0), (1, 1)),
        (m[2:, 1:-1], (1, 1), (0, 1)),
        (m[1:-1, :-2], (0, 1), (0, 0)),
    )
    for nb, a, b in sides:
        ys, xs = np.nonzero(inner & ~nb)
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
            edges.setdefault((x + a[0], y + a[1]), []).append((x + b[0], y + b[1]))
    loops = []
    while edges:
        start = next(iter(edges))
        loop, cur = [start], start
        while cur in edges:
            nxt = edges[cur].pop()
            if not edges[cur]:
                del edges[cur]
            if nxt == start and start not in edges:
                break
            loop.append(nxt)
            cur = nxt
        loops.append(np.array(loop, float))
    return loops


def _dp(pts, tol):
    """Douglas-Peucker on an open polyline: the indices kept."""
    keep = np.zeros(len(pts), bool)
    keep[[0, -1]] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        d = pts[j] - pts[i]
        n = math.hypot(*d)
        seg = pts[i + 1 : j] - pts[i]
        dist = np.hypot(*seg.T) if n == 0 else np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / n
        k = int(dist.argmax())
        if dist[k] > tol:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return np.nonzero(keep)[0]


def _simplify(loop, tol):
    """A closed loop with the points that straighten it within `tol` pixels dropped."""
    if len(loop) < 4:
        return loop
    far = int(np.hypot(*(loop - loop[0]).T).argmax())
    first, second = loop[: far + 1], np.vstack([loop[far:], loop[:1]])
    a = first[_dp(first, tol)]
    b = second[_dp(second, tol)]
    return np.vstack([a[:-1], b[:-1]])


def _triangulate(mask, dt, loops, tol, step):
    """Triangles over the sprite: the outline simplified to `tol`, plus a grid of inner points every `step` pixels."""
    from scipy.spatial import Delaunay

    pts = [_simplify(lp, tol) for lp in loops]
    if step:
        gy, gx = np.mgrid[step / 2 : mask.shape[0] : step, step / 2 : mask.shape[1] : step]
        gy, gx = gy.ravel(), gx.ravel()
        iy, ix = gy.astype(int), gx.astype(int)
        ok = mask[iy, ix] & (dt[iy, ix] >= max(1.5, 0.6 * step))
        pts.append(np.stack([gx[ok], gy[ok]], axis=1))
    p = np.unique(np.vstack(pts), axis=0)
    if len(p) < 3:
        return p, np.zeros((0, 3), int)
    tri = Delaunay(p).simplices
    c = p[tri].mean(axis=1)
    inside = mask[
        np.clip(c[:, 1].astype(int), 0, mask.shape[0] - 1), np.clip(c[:, 0].astype(int), 0, mask.shape[1] - 1)
    ]
    return p, tri[inside]


def mesh_of(rgba, triangles=5000):
    """The points and top triangles of a closed mesh of about `triangles` triangles in all (the top, the same flat
    bottom and the walls). The top is a Delaunay triangulation of the simplified outline and a grid of inner
    points; the grid is made as fine as the budget allows."""
    mask = rgba[..., 3] >= 128
    dt = ndimage.distance_transform_edt(mask)
    loops = _loops(mask)

    def total(tri):
        return 2 * len(tri) + 2 * len(_boundary(tri))

    def clean(found):
        return found[0], _unpinch(found[0], _face_up(found[0], found[1]))

    for tol in (0.5, 0.8, 1.2, 2.0, 3.0, 5.0):  # the finest outline whose triangles alone fit the budget
        best = clean(_triangulate(mask, dt, loops, tol, 0))
        if total(best[1]) > triangles:
            continue
        lo, hi = 1.0, float(max(mask.shape))
        for _ in range(14):
            mid = (lo + hi) / 2
            found = clean(_triangulate(mask, dt, loops, tol, mid))
            if total(found[1]) > triangles:
                lo = mid
            else:
                hi, best = mid, found
        return best
    return clean(_triangulate(mask, dt, loops, 5.0, 0))


def _face_up(p, tri):
    """The triangles turned all the same way, the one that makes the top face up (see build_mesh)."""
    a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return np.where((cross > 0)[:, None], tri[:, [0, 2, 1]], tri)


def _unpinch(p, tri):
    """Triangles removed where the outline touches itself at a point (two parts meeting at a vertex), the smallest
    one at that vertex first, so the walls never share an edge between two parts."""
    for _ in range(len(tri)):
        edges = _boundary(tri)
        count = np.bincount(edges[:, 0], minlength=len(p))
        pinch = np.nonzero(count > 1)[0]
        if not len(pinch):
            break
        near = np.nonzero((tri == pinch[0]).any(axis=1))[0]
        a, b, c = (p[tri[near, i]] for i in range(3))
        area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
        tri = np.delete(tri, near[int(area.argmin())], axis=0)
    return tri


def _boundary(tri):
    """The directed edges that belong to one triangle only, as (n, 2) vertex indices."""
    e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    return e[cnt[inv.ravel()] == 1]


def _uvs(p, mask):
    """Texture coordinates of points in sprite pixels. A point on the outline may lie on a transparent pixel (the
    outline runs along pixel corners), so it takes the centre of the nearest opaque one instead."""
    h, w = mask.shape
    _, (ny, nx) = ndimage.distance_transform_edt(~mask, return_indices=True)
    iy = np.clip(p[:, 1].astype(int), 0, h - 1)
    ix = np.clip(p[:, 0].astype(int), 0, w - 1)
    out = ~mask[iy, ix]
    x, y = p[:, 0].copy(), p[:, 1].copy()
    x[out], y[out] = nx[iy[out], ix[out]] + 0.5, ny[iy[out], ix[out]] + 0.5
    return np.stack([x / w, y / h], axis=1)


def build_mesh(rgba, z, triangles=5000, base=0.0, scale=None):
    """Flat-shaded triangles of a closed mesh: (positions (n, 3, 3), normals (n, 3), uvs (n, 3, 2)).
    x to the right, y up, z toward the bottom of the sprite (its nose is at -z); `scale` units per sprite pixel."""
    h, w = z.shape
    scale = scale or 1.0 / max(h, w)
    p, tri = mesh_of(rgba, triangles)
    from scipy.ndimage import map_coordinates

    height = map_coordinates(z, [p[:, 1] - 0.5, p[:, 0] - 0.5], order=1, mode="nearest") + base
    top = np.stack([(p[:, 0] - w / 2) * scale, height * scale, (p[:, 1] - h / 2) * scale], axis=1)
    flat = top.copy()
    flat[:, 1] = 0.0
    uv = _uvs(p, rgba[..., 3] >= 128)
    edges = _boundary(tri)
    tris, uvs = [top[tri]], [uv[tri]]
    tris.append(flat[tri[:, [0, 2, 1]]])  # the bottom, seen from below
    uvs.append(uv[tri[:, [0, 2, 1]]])
    u, v = edges[:, 0], edges[:, 1]  # walls: (top u, bottom u, bottom v) and (top u, bottom v, top v)
    tris += [np.stack([top[u], flat[u], flat[v]], axis=1), np.stack([top[u], flat[v], top[v]], axis=1)]
    uvs += [np.stack([uv[u], uv[u], uv[v]], axis=1), np.stack([uv[u], uv[v], uv[v]], axis=1)]
    pos, uvs = np.concatenate(tris), np.concatenate(uvs)
    n = np.cross(pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0])
    length = np.linalg.norm(n, axis=1)
    keep = length > 1e-12  # a wall where the hull is flat has no area
    n = n[keep] / length[keep, None]
    return pos[keep], n, uvs[keep]


def write_glb(path, pos, nrm, uvs, png, unlit=False):
    """A binary glTF 2.0 file: one flat-shaded mesh, the sprite as its texture with nearest-neighbour filtering."""
    import struct

    n = len(pos)
    arrays = [
        pos.reshape(-1, 3).astype("<f4").tobytes(),
        np.repeat(nrm, 3, axis=0).astype("<f4").tobytes(),
        uvs.reshape(-1, 2).astype("<f4").tobytes(),
        png,
    ]
    views, off = [], 0
    blob = b""
    for i, a in enumerate(arrays):
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(a), **({"target": 34962} if i < 3 else {})})
        pad = (-len(a)) % 4
        blob += a + b"\0" * pad
        off += len(a) + pad
    flat = pos.reshape(-1, 3)
    mat = {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 1.0}}
    doc = {
        "asset": {"version": "2.0", "generator": "Sprite Studio pxmesh"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2}, "material": 0}]}],
        "materials": [mat],
        "textures": [{"sampler": 0, "source": 0}],
        "samplers": [{"magFilter": 9728, "minFilter": 9728, "wrapS": 33071, "wrapT": 33071}],
        "images": [{"bufferView": 3, "mimeType": "image/png"}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": n * 3,
                "type": "VEC3",
                "min": flat.min(axis=0).tolist(),
                "max": flat.max(axis=0).tolist(),
            },
            {"bufferView": 1, "componentType": 5126, "count": n * 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": n * 3, "type": "VEC2"},
        ],
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if unlit:
        mat["extensions"] = {"KHR_materials_unlit": {}}
        doc["extensionsUsed"] = ["KHR_materials_unlit"]
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        fh.write(struct.pack("<II", len(blob), 0x004E4942) + blob)


def mesh(src, dst, size=128, triangles=5000, thickness=0.12, detail=0.15, base=0.03, unlit=False, scale=None):
    """A top-down sprite as a closed low-poly 3D model (.glb) of about `triangles` triangles, textured with the
    sprite itself. base: a rim of this share of the size under the whole hull, so its edge is a wall, not a knife."""
    import io

    triangles = check_triangles(triangles)
    im = pxtool.fit_sprite(Image.open(src).convert("RGBA"), size)
    rgba = np.array(im)
    z = relief(rgba, thickness, detail)
    pos, nrm, uvs = build_mesh(rgba, z, triangles, base=base * max(im.size), scale=scale)
    # the texture: transparent pixels take the colour of the nearest opaque one, so no lookup or mip level
    # near the outline can read a black or empty texel
    solid = rgba[..., 3] >= 128
    _, (ny, nx) = ndimage.distance_transform_edt(~solid, return_indices=True)
    tex = rgba[ny, nx].copy()
    tex[..., 3] = 255
    png = io.BytesIO()
    Image.fromarray(tex, "RGBA").save(png, "PNG")
    write_glb(str(dst), pos, nrm, uvs, png.getvalue(), unlit=unlit)
    return len(pos)


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
    m = sub.add_parser("mesh", help="a 3D model (.glb)")
    m.add_argument("src")
    m.add_argument("dst")
    m.add_argument("--size", type=int, default=128, help="the sprite is fitted to this size first")
    m.add_argument(
        "--triangles",
        type=int,
        default=5000,
        help=f"about this many triangles in all, {MIN_TRIANGLES} to {MAX_TRIANGLES} (set PX_MESH_MAX_TRIANGLES to "
        "change the limit); a small sprite cannot make more than its pixels allow, --size larger gives it more",
    )
    m.add_argument("--thickness", type=float, default=0.12, help="height of the hull, as a share of the size")
    m.add_argument("--detail", type=float, default=0.15, help="how much the sprite's light bumps the surface")
    m.add_argument("--base", type=float, default=0.03, help="height of the rim under the hull, as a share of the size")
    m.add_argument("--scale", type=float, default=None, help="units per sprite pixel (default: 1 / size)")
    m.add_argument("--unlit", action="store_true", help="an unlit material (flat colours)")
    a = ap.parse_args()
    if a.cmd == "mesh":
        try:
            check_triangles(a.triangles)
        except ValueError as e:
            ap.error(str(e))
        n = mesh(
            a.src,
            a.dst,
            size=a.size,
            triangles=a.triangles,
            thickness=a.thickness,
            detail=a.detail,
            base=a.base,
            unlit=a.unlit,
            scale=a.scale,
        )
        print(f"{a.dst}: {n} triangles (asked for {a.triangles})")
        return 0
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
