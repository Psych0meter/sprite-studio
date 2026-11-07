#!/usr/bin/env python3
"""pxtool: image helpers of Sprite Studio (pixel grid, clean-up, colours, palettes).

  pxtool.py describe <sprite>... [--glows] [--neutral]   colour words for the prompt
  pxtool.py init     <sprite> <out.png> [--size 64] [--mask MASK] [--canvas 512]
  pxtool.py post     <render.png> <out.png> [--size 128] [--grid INIT.json] [--mirror auto --orig SPRITE]
                     [--max-colours 64] [--edge 0.5] [--no-outline] [--min-part 0.004] [--key-tol 70]
                     [--keep-green] [--no-tidy] [--keep-mask MASK --base SPRITE] [--legacy --margin 2]
  pxtool.py sheet    <out.png> <folder>... [--cell 160]
  pxtool.py palette-extract | palette-apply | palette-autotune | palette-words | palette-export

`init` puts a sprite on the flat green background the LoRA was trained on,
scaled up by a whole factor (512 // size: 8 for 64 px, 4 for 128 px), to
start a generation from; it writes the pixel grid next to it.

`post` turns a render into a sprite: the green background removed by
flood fill from the border (so light pixels of the sprite stay), green shadows and
edge blends removed, holes inside the sprite filled, small loose specks
dropped (real side parts kept), the render's own colours kept (64 at most),
one sprite pixel per block of the pixel grid (majority vote, the grid's
phase matched to the render), lone pixels tidied, a dark outline, an optional mirror. It also writes
a 4x preview and prints the sprite-standard checks.

`sheet` lays out an original and its variants for comparison. The palette-*
commands make, apply and export the palettes of the web app (see below).

Needs numpy, pillow, scipy.
"""

import argparse
import colorsys
import json
import math
import os
import re
import sys
from collections import Counter

import numpy as np
from PIL import Image
from scipy import ndimage

# The flat background the LoRA was trained on, removed after generation.
KEY = "#00ff00"


def hex_to_rgb(h):
    h = h.strip().lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(c):
    return "#{:02x}{:02x}{:02x}".format(*tuple(int(v) for v in c[:3]))


def opaque_colours(paths):
    cnt = Counter()
    for p in paths:
        a = np.array(Image.open(p).convert("RGBA")).reshape(-1, 4)
        a = a[a[:, 3] > 200][:, :3]
        for c, n in zip(*np.unique(a, axis=0, return_counts=True), strict=False):
            cnt[tuple(int(v) for v in c)] += int(n)
    return cnt


def _quantize(cnt, k):
    """`k` colours for a {colour: count} set, median cut weighted by
    sqrt(count) so rare shades are not swamped by the big areas."""
    if k <= 0 or not cnt:
        return []
    if len(cnt) <= k:
        return list(cnt)
    data = []
    for c, n in cnt.items():
        data += [c] * max(1, int(math.sqrt(n)))
    w = math.ceil(math.sqrt(len(data)))
    data += [data[0]] * (w * w - len(data))
    img = Image.new("RGB", (w, w))
    img.putdata(data)
    q = img.quantize(k, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette()[: k * 3]
    used = sorted(set(np.array(q).reshape(-1).tolist()))
    return [tuple(pal[i * 3 : i * 3 + 3]) for i in used]


def _is_glow(c):
    return _sat(c) > 0.45 and _val(c) > 0.45


def palette_of(cnt, max_colours=64):
    """Up to `max_colours` colours. The glows (bright saturated colours) get
    slots of their own (6 to 20, by how much of the art they are) so cores
    and lights keep their brightest shades instead of being merged into the
    body's mid-tones."""
    if len(cnt) <= max_colours:
        cols = list(cnt)
    else:
        glow = {c: n for c, n in cnt.items() if _is_glow(c)}
        rest = {c: n for c, n in cnt.items() if not _is_glow(c)}
        gw = sum(math.sqrt(n) for n in glow.values())
        tw = gw + sum(math.sqrt(n) for n in rest.values())
        k_glow = 0 if not glow else int(np.clip(round(max_colours * 2 * gw / max(tw, 1)), 6, 20))
        k_glow = min(k_glow, len(glow), max_colours // 2)
        cols = _quantize(glow, k_glow) + _quantize(rest, max_colours - k_glow)
    return sorted(set(cols), key=lambda c: sum(c))[:max_colours]


def _hsv(c):
    return colorsys.rgb_to_hsv(*(v / 255.0 for v in c))


def _sat(c):
    return _hsv(c)[1]


def _val(c):
    return _hsv(c)[2]


def hue_name(c):
    h, s, v = _hsv(c)
    if s < 0.12:
        base = "grey"
    else:
        deg = h * 360
        for limit, name in [
            (15, "red"),
            (40, "orange"),
            (65, "yellow"),
            (160, "green"),
            (200, "cyan"),
            (250, "blue"),
            (290, "violet"),
            (330, "magenta"),
            (361, "red"),
        ]:
            if deg < limit:
                base = name
                break
        if s < 0.35:
            base = {"red": "mauve", "magenta": "mauve", "violet": "grey-violet"}.get(base, "greyish " + base)
    tone = "dark " if v < 0.35 else ("light " if v > 0.75 else "")
    return tone + base


def _family(c):
    return hue_name(c).replace("dark ", "").replace("light ", "")


def describe_glows(paths, min_share=0.05):
    """Every glow family that is a real part of the sprites, for a sprite that
    carries several groups' colours: "red, orange, blue and violet"."""
    cnt = opaque_colours(paths)
    glow = Counter()
    for c, n in cnt.items():
        if _val(c) > 0.45 and _sat(c) > 0.45:
            glow[_family(c)] += n
    total = sum(glow.values()) or 1
    names = [f for f, n in glow.most_common() if n / total >= min_share]
    if len(names) > 1:
        names = names[:-1] + ["and " + names[-1]]
    return (", " if len(names) > 2 else " ").join(names)


def sheet(dst, rows, cell=160):
    """A comparison sheet: one row per sprite folder, its original first (the
    file orig.png) then every variant, on a dark background, upscaled with
    nearest so the pixels stay crisp."""
    import glob as _glob
    import os

    rows = [r for r in rows if os.path.isdir(r)]
    data = []
    for r in sorted(rows):
        files = [os.path.join(r, "orig.png")] if os.path.exists(os.path.join(r, "orig.png")) else []
        files += sorted(
            f
            for f in _glob.glob(os.path.join(r, "*.png"))
            if not f.endswith("_x4.png") and os.path.basename(f) not in ("orig.png", "init.png")
        )
        if files:
            data.append((os.path.basename(r.rstrip("/")), files))
    if not data:
        print("nothing to show")
        return
    cols = max(len(f) for _, f in data)
    out = Image.new("RGBA", (cols * cell, len(data) * (cell + 14)), (34, 34, 44, 255))
    from PIL import ImageDraw

    dr = ImageDraw.Draw(out)
    for j, (name, files) in enumerate(data):
        y = j * (cell + 14)
        dr.text((4, y + 1), name, fill=(220, 220, 220, 255))
        for i, f in enumerate(files):
            im = Image.open(f).convert("RGBA")
            s = max(1, (cell - 8) // max(im.size))
            im = im.resize((im.width * s, im.height * s), Image.Resampling.NEAREST)
            if im.width > cell - 8 or im.height > cell - 8:
                im.thumbnail((cell - 8, cell - 8), Image.Resampling.NEAREST)
            out.alpha_composite(im, (i * cell + (cell - im.width) // 2, y + 14 + (cell - im.height) // 2))
    out.save(dst)
    print(dst, f"{len(data)} rows")


def describe(paths, neutral=False):
    """Colour words for the prompt, from colour families (summed over every
    shade): the body (the most used family that is not near black), light
    plating if a lot of it is near white, and the glow (the most used
    saturated bright family). neutral: words for things that are not vehicles
    (items, rocks, icons): "body" and "highlights" instead of "metal hull"
    and "armour plating"."""
    return describe_counts(opaque_colours(paths), neutral)


def describe_counts(cnt, neutral=False):
    """describe() for a {colour: count} set (a sprite's pixels, a palette's weights)."""
    total = sum(cnt.values())
    body, light, glow = Counter(), 0, Counter()
    for c, n in cnt.items():
        _h, s, v = _hsv(c)
        if v > 0.7 and s < 0.2:
            light += n
        elif v > 0.45 and s > 0.45:
            glow[_family(c)] += n
        elif v > 0.1:
            body[_family(c)] += n
    parts = []
    if body:
        parts.append("dark " + body.most_common(1)[0][0] + (" body" if neutral else " metal hull"))
    if light > 0.12 * total:
        parts.append("white highlights" if neutral else "white armour plating")
    parts.append("near-black shadows")
    if glow:
        parts.append("bright glowing " + glow.most_common(1)[0][0] + " lights, high contrast")
    return ", ".join(parts)


# ---------------------------------------------------------------- palettes
#
# A palette is a JSON file: {"id", "label", "colours": ["#rrggbb", ...],
# "weights": [n, ...] (how much of the art each colour is, optional),
# "tune": {"hue", "saturation", "brightness", "contrast"} (-100..100, hue in
# degrees), "mode": "tune" | "snap" | "match", "words": true (colour words of
# the prompt from the palette)}. Applied to a finished sprite:
#   tune   the sprite's own colours, only adjusted by the tuning;
#   snap   every pixel takes the nearest colour of the (tuned) palette;
#   match  the sprite's light and shade are first spread like the palette's
#          (a dark render gets the palette's brightness), then snapped.
# The web app's preview (app.js applyPalette) does the same arithmetic: keep
# the two in step.

TUNE_KEYS = ("hue", "saturation", "brightness", "contrast")


def load_palette(path):
    """A palette from .json, .hex (one colour per line, Lospec), .gpl (GIMP) or
    an image (its colours, 64 at most)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path) as fh:
            pal = json.load(fh)
    elif ext in (".hex", ".txt"):
        with open(path) as fh:
            cols = [ln.strip() for ln in fh if re.fullmatch(r"#?[0-9a-fA-F]{6}", ln.strip())]
        pal = {"colours": ["#" + c.lstrip("#").lower() for c in cols]}
    elif ext == ".gpl":
        cols = []
        with open(path) as fh:
            for line in fh:
                m = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)", line)
                if m:
                    cols.append(rgb_to_hex([int(m.group(i)) for i in (1, 2, 3)]))
        pal = {"colours": cols}
    else:
        pal = extract_palette([path], 64)
    pal.setdefault("id", re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(os.path.basename(path))[0].lower()).strip("_"))
    pal.setdefault("label", pal["id"])
    pal.setdefault("colours", [])
    pal.setdefault("weights", [])
    pal.setdefault("tune", {})
    pal.setdefault("mode", "snap" if pal["colours"] else "tune")
    pal.setdefault("words", False)
    for k in TUNE_KEYS + ("light",):
        pal["tune"].setdefault(k, 0)
    return pal


def extract_palette(paths, n=32):
    """The colours (n at most) of some sprites, with how much each is used."""
    cnt = opaque_colours(paths)
    cols = palette_of(cnt, n) if cnt else []
    weights = [0] * len(cols)
    if cols:
        arr = np.array(cols)
        for c, k in cnt.items():
            i = int((((arr - np.array(c)) ** 2) * np.array([3, 4, 2])).sum(1).argmin())
            weights[i] += k
    return {
        "colours": [rgb_to_hex(c) for c in cols],
        "weights": weights,
        "tune": dict.fromkeys(TUNE_KEYS, 0),
        "mode": "snap",
        "words": False,
    }


def tune_rgb(rgb, tune):
    """rgb: (..., 3) uint8. Hue turned, saturation scaled, brightness as a
    gamma on the value (+50: darks lifted a lot, -50: darkened), contrast
    around mid-grey; in HSV."""
    t = {k: float((tune or {}).get(k, 0) or 0) for k in TUNE_KEYS}
    if not any(t.values()):
        return rgb
    x = rgb.astype(np.float64) / 255.0
    mx, mn = x.max(-1), x.min(-1)
    d = mx - mn
    v = mx
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1), 0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    dd = np.where(d > 0, d, 1)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) / 6.0
    h = np.where(d > 0, h, 0)
    h = (h + t["hue"] / 360.0) % 1.0
    s = np.clip(s * (1 + t["saturation"] / 100.0), 0, 1)
    v = np.power(v, 2.0 ** (-t["brightness"] / 50.0))
    v = np.clip((v - 0.5) * (1 + t["contrast"] / 100.0) + 0.5, 0, 1)
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, u = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    out = np.stack(
        [
            np.choose(i, [v, q, p, p, u, v]),
            np.choose(i, [u, v, v, q, p, p]),
            np.choose(i, [p, p, u, v, v, q]),
        ],
        -1,
    )
    return np.clip(np.floor(out * 255 + 0.5), 0, 255).astype(np.uint8)  # rounds like Math.round (app.js)


def _lum(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def relight(canvas, strength):
    """Light from the top (strength > 0, up to 100) or from below (< 0): every
    opaque pixel near a top edge of the silhouette is brightened and near a
    bottom edge darkened, by how close it is to that edge (within r pixels,
    r = the sprite's height / 12, 2 at least); the inside is left as it is.
    Sprites painted lit from below have their highlights on the lower edges:
    this turns them back. app.js relight() does the same arithmetic."""
    s = float(strength or 0) / 100.0
    op = canvas[..., 3] > 0
    if not s or not op.any():
        return canvas
    rows = np.where(op.any(1))[0]
    r = max(2, int(np.floor((rows[-1] - rows[0] + 1) / 12 + 0.5)))
    H = op.shape[0]
    up = np.full(op.shape, r)
    down = np.full(op.shape, r)
    found_u = np.zeros(op.shape, bool)
    found_d = np.zeros(op.shape, bool)
    for k in range(1, r + 1):
        above = np.zeros(op.shape, bool)  # transparent (or outside) k pixels above
        above[:k] = True
        above[k:] = ~op[:-k] if k < H else True
        below = np.zeros(op.shape, bool)
        below[H - k :] = True
        below[: H - k] = ~op[k:] if k < H else True
        nu = above & ~found_u
        up[nu] = k
        found_u |= above
        nd = below & ~found_d
        down[nd] = k
        found_d |= below
    shade = (down - up) / (r - 1)
    f = 1.0 + s * shade
    out = canvas.copy()
    rgb = out[..., :3].astype(np.float64) * f[..., None]
    out[..., :3] = np.where(op[..., None], np.clip(np.floor(rgb + 0.5), 0, 255), out[..., :3]).astype(np.uint8)
    return out


def apply_palette(canvas, pal):
    """The palette on a finished RGBA sprite (uint8 array), in place of its colours."""
    op = canvas[..., 3] > 0
    if not op.any():
        return canvas
    canvas = relight(canvas, (pal.get("tune") or {}).get("light", 0))
    out = canvas.copy()
    px = out[op][:, :3]
    mode = pal.get("mode", "snap")
    if mode == "tune" or not pal.get("colours"):
        px = tune_rgb(px, pal.get("tune"))
    else:
        cols = tune_rgb(np.array([hex_to_rgb(c) for c in pal["colours"]], dtype=np.uint8), pal.get("tune"))
        if mode == "match":
            w = np.array(pal.get("weights") or [1] * len(cols), dtype=np.float64)
            w = w if len(w) == len(cols) and w.sum() > 0 else np.ones(len(cols))
            pl = _lum(cols.astype(np.float64))
            order = np.argsort(pl, kind="stable")
            cum = np.cumsum(w[order]) / w.sum()
            lum = _lum(px.astype(np.float64))
            rank = np.empty(len(lum))
            rank[np.argsort(lum, kind="stable")] = (np.arange(len(lum)) + 0.5) / len(lum)
            target = pl[order][np.minimum(np.searchsorted(cum, rank), len(cols) - 1)]
            f = px.astype(np.float64)
            scaled = f * (target / np.maximum(lum, 1.0))[:, None]
            dark = lum < 1.0
            scaled[dark] = target[dark, None]
            px = np.clip(np.floor(scaled + 0.5), 0, 255).astype(np.uint8)
        px = _snap(px[None], cols.astype(np.int32))[0]
    out[op, :3] = px
    return out


def _opaque_pixels(paths, limit=20000):
    """The opaque pixels of some sprites (an even sample when there are many)."""
    parts = []
    for p in paths:
        a = np.array(Image.open(p).convert("RGBA")).reshape(-1, 4)
        parts.append(a[a[:, 3] > 127][:, :3])
    px = np.concatenate(parts) if parts else np.zeros((0, 3), np.uint8)
    if len(px) > limit:
        px = px[np.linspace(0, len(px) - 1, limit).astype(int)]
    return px


def colour_stats(px):
    """Mean and spread of the value (HSV), mean saturation, of (N, 3) uint8 pixels."""
    if not len(px):
        return {"value": 0.0, "spread": 0.0, "saturation": 0.0}
    x = px.astype(np.float64) / 255.0
    mx, mn = x.max(1), x.min(1)
    s = np.where(mx > 0, (mx - mn) / np.where(mx > 0, mx, 1), 0)
    return {"value": float(mx.mean()), "spread": float(mx.std()), "saturation": float(s.mean())}


def auto_tune(px, target, hue=0.0):
    """The tuning (brightness, contrast, saturation) that gives pixels `px` the
    value, spread and saturation of `target` (colour_stats of the reference):
    each setting adjusted in turn by bisection, a few rounds, measured with
    tune_rgb itself so the result is what generation will make."""
    t = {"hue": hue, "saturation": 0.0, "brightness": 0.0, "contrast": 0.0}

    def stat(key, val):
        t2 = dict(t, **{key: val})
        return colour_stats(tune_rgb(px, t2))

    goals = (
        ("brightness", "value", -100, 100),
        ("contrast", "spread", -100, 100),
        ("saturation", "saturation", -100, 100),
    )
    for _ in range(4):
        for key, measure, lo, hi in goals:
            want = target[measure]
            a, b = lo, hi
            for _ in range(14):  # every one of these grows with its setting
                mid = (a + b) / 2
                if stat(key, mid)[measure] < want:
                    a = mid
                else:
                    b = mid
            t[key] = round((a + b) / 2)
    return t


def palette_words(pal, neutral=False):
    cols = tune_rgb(np.array([hex_to_rgb(c) for c in pal.get("colours", [])], dtype=np.uint8), pal.get("tune"))
    w = pal.get("weights") or [1] * len(cols)
    if len(w) != len(cols):
        w = [1] * len(cols)
    return (
        describe_counts(
            Counter({tuple(int(v) for v in c): int(n) or 1 for c, n in zip(cols, w, strict=False)}), neutral
        )
        if len(cols)
        else ""
    )


def recolour(src, dst, pal):
    """The palette on an existing sprite file (a result, a source sprite)."""
    a = np.array(Image.open(src).convert("RGBA"))
    a[..., 3] = np.where(a[..., 3] > 127, 255, 0)
    img = Image.fromarray(apply_palette(a, pal), "RGBA")
    img.save(dst)
    img.resize((img.width * 4, img.height * 4), Image.Resampling.NEAREST).save(dst.rsplit(".", 1)[0] + "_x4.png")


def export_palette(pal, fmt):
    """The palette as text (.hex, .gpl) or PNG bytes (one 8x8 swatch per colour)."""
    cols = (
        [
            rgb_to_hex(c)
            for c in tune_rgb(np.array([hex_to_rgb(c) for c in pal["colours"]], dtype=np.uint8), pal.get("tune"))
        ]
        if pal.get("colours")
        else []
    )
    if fmt == "hex":
        return "\n".join(c.lstrip("#") for c in cols) + "\n"
    if fmt == "gpl":
        lines = ["GIMP Palette", f"Name: {pal.get('label', pal.get('id', 'palette'))}", "Columns: 8", "#"]
        lines += [f"{r:3d} {g:3d} {b:3d}\t{c}" for c in cols for r, g, b in [hex_to_rgb(c)]]
        return "\n".join(lines) + "\n"
    if fmt == "png":
        import io

        img = Image.new("RGB", (8 * max(1, len(cols)), 8))
        for i, c in enumerate(cols):
            img.paste(hex_to_rgb(c), (i * 8, 0, i * 8 + 8, 8))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    raise ValueError(f"unknown format {fmt}")


def pixel_scale(size, canvas=512):
    """The whole number of render pixels per sprite pixel: the training
    images, the init images and the clean-up all use the same one, so the
    model's pixels land on the grid the clean-up reads."""
    return max(1, canvas // max(1, size))


def fit_sprite(im, size):
    """The sprite on a size x size canvas: kept as it is when it already has
    that size, otherwise its content scaled (nearest) to fill about 88 % of
    it, like the project's own sprites."""
    if max(im.size) == size:
        return im
    box = im.getbbox() or (0, 0, im.width, im.height)
    im = im.crop(box)
    fit = size - 2 * max(1, round(size * 0.06))
    s = fit / max(im.size)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.NEAREST)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
    return out


def grid_for(w, h, canvas=512, scale=None):
    k = scale or pixel_scale(max(w, h), canvas)
    return {"k": k, "w": w, "h": h, "ox": (canvas - w * k) // 2, "oy": (canvas - h * k) // 2, "canvas": canvas}


def prepare_init(src, dst, key=KEY, canvas=512, size=None, mask=None):
    """The source sprite scaled up by a whole factor on the key colour, so the
    model starts from the right silhouette, on the right background, on the
    pixel grid the clean-up will read. Writes <dst>.json (the grid) and
    <dst>_base.png (the sprite at its final size, for touch-ups). With a
    mask (white = redo, at the sprite's final size), also <dst>_mask.png,
    the mask at render size for the sampler."""
    im = Image.open(src).convert("RGBA")
    if size:
        im = fit_sprite(im, size)
    g = grid_for(im.width, im.height, canvas)
    k = g["k"]
    big = im.resize((im.width * k, im.height * k), Image.Resampling.NEAREST)
    out = Image.new("RGBA", (canvas, canvas), hex_to_rgb(key) + (255,))
    out.alpha_composite(big, (g["ox"], g["oy"]))
    out.convert("RGB").save(dst)
    stem = dst.rsplit(".", 1)[0]
    im.save(stem + "_base.png")
    with open(dst + ".json", "w") as fh:
        json.dump(g, fh)
    if mask:
        m = Image.open(mask).convert("L")
        if m.size != im.size:
            m = m.resize(im.size, Image.Resampling.NEAREST)
        a = np.array(m) > 127
        a = ndimage.binary_dilation(a, iterations=1)  # a little context around the strokes
        Image.fromarray((a * 255).astype(np.uint8), "L").save(stem + "_mask_small.png")
        big = np.zeros((canvas, canvas), np.uint8)
        big[g["oy"] : g["oy"] + im.height * k, g["ox"] : g["ox"] + im.width * k] = (
            np.kron(a, np.ones((k, k))).astype(np.uint8) * 255
        )
        Image.fromarray(big, "L").convert("RGB").save(stem + "_mask.png")
    return g


def _snap(rgb, pal):
    """Nearest palette colour for every pixel (weighted RGB distance). Each distinct colour is looked up once:
    a render has far fewer colours than pixels, and the clean-up is re-run live while settings are dragged."""
    flat = rgb.reshape(-1, 3).astype(np.int32)
    codes = (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]
    uniq, first, inv = np.unique(codes, return_index=True, return_inverse=True)
    p = pal.astype(np.int32)
    wts = np.array([3, 4, 2])
    out = np.empty((len(uniq), 3), np.uint8)
    for i in range(0, len(uniq), 4096):
        u = flat[first[i : i + 4096]]
        d = (((u[:, None, :] - p[None, :, :]) ** 2) * wts).sum(-1)
        out[i : i + 4096] = p[d.argmin(1)]
    return out[inv.reshape(-1)].reshape(rgb.shape)


def _key_like(rgb, key, tol, min_sat=0.3):
    """Background pixels: close to the key colour, or of the key's hue at any
    brightness (the soft shadows and edge blends the model paints onto the
    key colour are darker or paler versions of it)."""
    hue_tol = 35.0
    k = np.array(hex_to_rgb(key))
    close = np.linalg.norm(rgb.astype(int) - k, axis=-1) < tol
    f = rgb.astype(float) / 255.0
    mx, mn = f.max(-1), f.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    d = np.maximum(mx - mn, 1e-6)
    hue = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60.0
    kh = _hsv(k)[0] * 360.0
    dh = np.abs((hue - kh + 180) % 360 - 180)
    tinted = (dh < hue_tol) & (sat > min_sat) & (mx > 0.08)
    return close | tinted


def _luma(rgb):
    f = rgb.astype(float)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def _symmetric(path, threshold=0.9):
    """Whether a sprite is left-right symmetric (its silhouette overlaps its
    mirror image by `threshold` or more)."""
    a = np.array(Image.open(path).convert("RGBA"))[..., 3] > 0
    ys, xs = np.nonzero(a)
    if not len(xs):
        return False
    a = a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    m = a[:, ::-1]
    return (a & m).sum() / max(1, (a | m).sum()) >= threshold


def _tidy(canvas, pal, outline=True):
    """Pixel-art tidying at the final size: lone pixels that match none of
    their 8 neighbours take the neighbourhood's colour (the speckle the
    model leaves), and the edge of the silhouette gets a dark outline, as in
    the hand-drawn sprites (glows on the edge are kept)."""
    h, w = canvas.shape[:2]
    alpha = canvas[..., 3] > 0
    rgb = canvas[..., :3].astype(np.int64)
    code = (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]
    out = canvas.copy()
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if not alpha[y, x]:
                continue
            nb = code[y - 1 : y + 2, x - 1 : x + 2][alpha[y - 1 : y + 2, x - 1 : x + 2]]
            if len(nb) < 7:
                continue
            others = nb[nb != code[y, x]]
            if len(others) == len(nb) - 1:  # matches no neighbour
                v, c = np.unique(others, return_counts=True)
                if c.max() >= 3:
                    best = int(v[c.argmax()])
                    out[y, x, :3] = (best >> 16 & 255, best >> 8 & 255, best & 255)
    if not outline:
        return out
    darkest = pal[np.argmin(_luma(pal))]
    inner = ndimage.binary_erosion(alpha)
    edge = alpha & ~inner
    for y, x in zip(*np.nonzero(edge), strict=False):
        if not _is_glow(tuple(int(v) for v in out[y, x, :3])):
            out[y, x, :3] = darkest
    return out


def _grid_sample(rgb, fg, pal, g, search=True, edge=0.5):
    """One sprite pixel per k x k block of the render, on the grid of the
    init image (or of the training images): opaque when most of the block
    is body (edge: the share, 0.5 by default), coloured by the block's most
    common (palette-snapped) colour.
    The model may draw its pixels a little off that grid, so the phase that
    makes the blocks the most uniform is used."""
    k, w, h = g["k"], g["w"], g["h"]
    snapped = _snap(rgb, pal)
    codes = (snapped[..., 0].astype(np.int64) << 16) | (snapped[..., 1].astype(np.int64) << 8) | snapped[..., 2]
    H, W = fg.shape
    best = (g["ox"], g["oy"])
    if search and k >= 2:
        luma = _luma(rgb)
        scores = {}
        r = k // 2
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                ox, oy = g["ox"] + dx, g["oy"] + dy
                x0, y0 = max(ox, 0), max(oy, 0)
                nx, ny = (min(W, ox + w * k) - x0) // k, (min(H, oy + h * k) - y0) // k
                if nx < 1 or ny < 1:
                    continue
                blk = luma[y0 : y0 + ny * k, x0 : x0 + nx * k].reshape(ny, k, nx, k)
                m = fg[y0 : y0 + ny * k, x0 : x0 + nx * k].reshape(ny, k, nx, k)
                var = blk.var(axis=(1, 3))
                full = m.all(axis=(1, 3))
                scores[(ox, oy)] = var[full].mean() if full.any() else 1e9
        if scores:
            best = min(scores, key=scores.get)
    ox, oy = best
    out = np.zeros((h, w, 4), np.uint8)
    for j in range(h):
        for i in range(w):
            y0, x0 = oy + j * k, ox + i * k
            if y0 < 0 or x0 < 0 or y0 + k > H or x0 + k > W:
                continue
            m = fg[y0 : y0 + k, x0 : x0 + k]
            if m.mean() < edge:
                continue
            c = codes[y0 : y0 + k, x0 : x0 + k][m]
            v, cnts = np.unique(c, return_counts=True)
            b = int(v[cnts.argmax()])
            out[j, i] = (b >> 16 & 255, b >> 8 & 255, b & 255, 255)
    return out


def _legacy_fit(rgb, fg, pal, size, margin, mirror):
    """The first clean-up: crop the body, fit it into size - 2*margin px
    with a majority vote per block (blocks of any size, off the model's
    pixel grid: noisier than the grid sampling)."""
    ys, xs = np.nonzero(fg)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    rgb, fg = rgb[y0:y1, x0:x1], fg[y0:y1, x0:x1]
    h, w = fg.shape
    s = (size - 2 * margin) / max(h, w)
    oh, ow = max(1, round(h * s)), max(1, round(w * s))

    # 5. Majority vote per output pixel (snapped colours), opaque if most of
    #    its block is body.
    snapped = _snap(rgb, pal)
    codes = (snapped[..., 0].astype(np.int64) << 16) | (snapped[..., 1].astype(np.int64) << 8) | snapped[..., 2]
    out = np.zeros((oh, ow, 4), np.uint8)
    yb = np.linspace(0, h, oh + 1).astype(int)
    xb = np.linspace(0, w, ow + 1).astype(int)
    for j in range(oh):
        for i in range(ow):
            m = fg[yb[j] : yb[j + 1], xb[i] : xb[i + 1]]
            if m.size == 0 or m.mean() < 0.5:
                continue
            c = codes[yb[j] : yb[j + 1], xb[i] : xb[i + 1]][m]
            v, cnts = np.unique(c, return_counts=True)
            best = int(v[cnts.argmax()])
            out[j, i] = (best >> 16 & 255, best >> 8 & 255, best & 255, 255)

    # 6. Optional symmetry: one half mirrored onto the other.
    if mirror in ("left", "right"):
        half = ow // 2
        if mirror == "left":
            out[:, ow - half :] = out[:, :half][:, ::-1]
        else:
            out[:, :half] = out[:, ow - half :][:, ::-1]

    canvas = np.zeros((size, size, 4), np.uint8)
    oy, ox = (size - oh) // 2, (size - ow) // 2
    canvas[oy : oy + oh, ox : ox + ow] = out
    return canvas


def flatten_background(src, dst, key=KEY, tol=70):
    """The render with its background made the pure key colour: the model's greens drift from image to image
    (a darker or bluer green), which makes a grid of previews look uneven. Only the green reached from the
    border changes, as in post()."""
    a = np.array(Image.open(src).convert("RGB"))
    near = _key_like(a, key, tol)
    lab, _ = ndimage.label(near)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(border))
    a[bg] = hex_to_rgb(key)
    Image.fromarray(a).save(dst)


def tile_post(src, dst, size, orig=None, grid=None, keep_edge=0, feather=4):
    """A render of a tile (a floor, a wall: opaque art that is not pixel art) made into the tile: the sprite's
    rectangle cut out of the render and scaled down to its size, in full colour (no background removal, no colour
    limit, no outline). With the original (the remaster's start) the outer `keep_edge` px stay the original's, so
    tiles that were made to join still join, blended into the new surface over `feather` px; its alpha is kept."""
    img = Image.open(src).convert("RGB")
    if grid:
        with open(grid) as fh:
            g = json.load(fh)
    else:
        g = grid_for(size, size, img.width)
    w, h, k = g["w"], g["h"], g["k"]
    rgb = np.asarray(
        img.crop((g["ox"], g["oy"], g["ox"] + w * k, g["oy"] + h * k)).resize((w, h), Image.Resampling.LANCZOS),
        dtype=np.float64,
    )
    alpha = np.full((h, w), 255, dtype=np.uint8)
    if orig and os.path.isfile(orig):
        o = np.asarray(Image.open(orig).convert("RGBA"))
        if o.shape[:2] == (h, w):
            alpha = o[..., 3].copy()
            if keep_edge > 0:
                yy, xx = np.mgrid[0:h, 0:w]
                dist = np.minimum.reduce([xx, yy, w - 1 - xx, h - 1 - yy])
                keep = np.clip((keep_edge + feather - dist) / feather, 0, 1)[..., None]
                rgb = o[..., :3] * keep + rgb * (1 - keep)
        else:
            print(f"warning: the original is {o.shape[1]}x{o.shape[0]}, not {w}x{h}: its edge is not kept")
    out = Image.fromarray(np.dstack([np.clip(np.rint(rgb), 0, 255).astype(np.uint8), alpha]), "RGBA")
    out.save(dst)
    out.resize((w * 4, h * 4), Image.Resampling.NEAREST).save(dst.rsplit(".", 1)[0] + "_x4.png")
    print(f"{dst}: {w}x{h} tile" + (f", the original's outer {keep_edge} px kept" if keep_edge and orig else ""))


def post(
    src,
    dst,
    key=KEY,
    size=128,
    min_part=0.004,
    mirror="none",
    key_tol=70,
    orig=None,
    tidy=True,
    margin=2,
    keep_green=False,
    grid=None,
    legacy=False,
    keep_mask=None,
    base=None,
    palette=None,
    fill=0,
    colours=64,
    outline=True,
    edge=0.5,
    tile=False,
    keep_edge=0,
):
    """tile: the render is a tile in full colour (see tile_post), keep_edge: px of the original's edge it keeps.
    keep_green: the sprite has green parts of its own (an acid pool...):
    only the green reached from the border is background, green inside the
    silhouette is kept. margin: empty pixels around the sprite (icons fill
    their whole canvas: 0; legacy fitting only). grid: the init's grid file
    (pxtool.py init writes it); without it the grid of a size x size sprite
    centred in the render is used, as in the training images. legacy: the
    old fitting (crop the body, scale it to the size). keep_mask + base: a
    touch-up, only the mask's white pixels come from the render, the rest
    stays the base sprite. fill: 50 to 100, the share of the size x size
    canvas the sprite's longest side takes (100: edge to edge); the drawing
    is cropped from the render and pixelated at that size, so it is neither
    cut by the canvas nor lost in empty space. 0: the render's own scale.
    Fine-tuning: min_part (loose parts dropped, share of the body), key_tol
    (how far from the key green a pixel is still background), colours (the
    most the sprite keeps, 2 to 64), edge (0.2 to 0.8: the share of a block
    that must be body to draw its pixel; lower = a fuller silhouette),
    outline (the dark edge line; tidy=False drops it too)."""
    if tile:
        return tile_post(src, dst, size, orig=orig, grid=grid, keep_edge=int(keep_edge or 0))
    a = np.array(Image.open(src).convert("RGBA"))
    rgb = a[..., :3]
    if mirror == "auto":
        mirror = "left" if orig and _symmetric(orig) else "none"

    # 1. Background: transparent pixels, plus everything green reached from
    #    the border (the key colour and its darker or paler shadows and
    #    blends). Light body pixels inside the silhouette are never touched.
    bg = a[..., 3] < 128
    if keep_green:
        # Only colours very close to the pure key: the sprite's own greens,
        # darker or paler, are never taken for background.
        near = np.linalg.norm(rgb.astype(int) - np.array(hex_to_rgb(key)), axis=-1) < 40
    else:
        near = _key_like(rgb, key, key_tol)
    lab, _ = ndimage.label(near | bg)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = bg | np.isin(lab, list(border))
    fg = ~bg

    # 2. Loose specks: drop the small separate bits, keep real side parts.
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        keep = [i + 1 for i, s in enumerate(sizes) if s >= min_part * sizes.max()]
        fg = np.isin(lab, keep)

    # 3. Holes inside the body: filled, coloured from the nearest body pixel.
    filled = ndimage.binary_fill_holes(fg)
    holes = filled & ~fg
    # Inside the silhouette, even faintly green pixels (the blend of body
    # and background at the edges) are repainted from the body.
    if not keep_green:
        holes |= filled & _key_like(rgb, key, key_tol, min_sat=0.12)
    if holes.any():
        solid = filled & ~holes
        _, (iy, ix) = ndimage.distance_transform_edt(~solid, return_indices=True)
        rgb = rgb.copy()
        rgb[holes] = rgb[iy[holes], ix[holes]]
    fg = filled
    # The render's own colours (64 at most): the LoRA already draws the
    # group's colours and contrast, the clean-up only keeps them.
    cols, counts = np.unique(rgb[fg], axis=0, return_counts=True)
    cnt = Counter({tuple(int(v) for v in c): int(n) for c, n in zip(cols, counts, strict=False)})
    colours = max(2, min(64, int(colours or 64)))
    pal = np.array(palette_of(cnt, colours)) if cnt else np.array([[0, 0, 0]])

    if fg.sum() < 64:
        print(f"{dst}: no sprite found in {src} (only background), skipped")
        return
    fill = max(0, min(100, int(fill or 0)))
    if fill and not keep_mask:
        long_side = max(1, round(size * max(fill, 50) / 100))
        ys, xs = np.nonzero(fg)
        per_px = max(ys.max() - ys.min() + 1, xs.max() - xs.min() + 1) / long_side
        if per_px < 2:
            print(
                f"warning: {src}: the drawing is small in the render ({per_px:.1f} render px per sprite px): "
                "filling the canvas enlarges it, expect soft pixels"
            )
        canvas = _legacy_fit(rgb, fg, pal, size, (size - long_side) / 2, mirror)
        ow = size
    elif not legacy:
        if grid:
            with open(grid) as fh:
                g = json.load(fh)
        else:
            g = grid_for(size, size, canvas=fg.shape[0])
        canvas = _grid_sample(rgb, fg, pal, g, edge=max(0.1, min(0.9, float(edge))))
        if not (canvas[..., 3] > 0).any():
            print(f"{dst}: nothing on the sprite grid in {src}, skipped")
            return
        ow, _oh = g["w"], g["h"]
        if mirror in ("left", "right"):
            half = ow // 2
            if mirror == "left":
                canvas[:, ow - half :] = canvas[:, :half][:, ::-1]
            else:
                canvas[:, :half] = canvas[:, ow - half :][:, ::-1]
    else:
        canvas = _legacy_fit(rgb, fg, pal, size, margin, mirror)
        ow = size
    if tidy:
        canvas = _tidy(canvas, pal, outline)
    # Last pass at the final size: any pixel still green is background
    # fringe; on the edge it goes, inside it takes the colour of the nearest
    # clean body pixel.
    alpha = canvas[..., 3] > 0
    tint = alpha & _key_like(canvas[..., :3], key, key_tol, min_sat=0.12)
    if keep_green:
        tint &= ~ndimage.binary_erosion(alpha)  # only the edge fringe
    if tint.any():
        edge = alpha & ~ndimage.binary_erosion(alpha)
        canvas[tint & edge] = 0
        inner = tint & ~edge
        clean = (canvas[..., 3] > 0) & ~tint
        if inner.any() and clean.any():
            _, (iy, ix) = ndimage.distance_transform_edt(~clean, return_indices=True)
            canvas[inner] = canvas[iy[inner], ix[inner]]
    if palette:
        pal = load_palette(palette) if isinstance(palette, str) else palette
        if not pal.get("colours") and not any(float(v or 0) for v in pal.get("tune", {}).values()):
            print(f"warning: the palette {pal.get('label', palette)} has no colours and no tuning: nothing changed")
        canvas = apply_palette(canvas, pal)
    if keep_mask and base:
        b = np.array(Image.open(base).convert("RGBA"))
        m = np.array(Image.open(keep_mask).convert("L")) > 127
        if b.shape[:2] == canvas.shape[:2] and m.shape == canvas.shape[:2]:
            canvas = np.where(m[..., None], canvas, b).astype(np.uint8)
            canvas[..., 3] = np.where(canvas[..., 3] > 127, 255, 0)
            op = canvas[..., 3] > 0
            cols, counts = np.unique(canvas[op][:, :3], axis=0, return_counts=True)
            if len(cols) > 64:  # back within the sprite standard
                cnt = Counter({tuple(int(v) for v in c): int(n) for c, n in zip(cols, counts, strict=False)})
                canvas[op, :3] = _snap(canvas[op][:, :3][None], np.array(palette_of(cnt, 64)))[0]
    img = Image.fromarray(canvas, "RGBA")
    img.save(dst)
    img.resize((img.width * 4, img.height * 4), Image.Resampling.NEAREST).save(dst.rsplit(".", 1)[0] + "_x4.png")

    opaque = canvas[canvas[..., 3] > 0][:, :3]
    ncol = len(np.unique(opaque, axis=0)) if len(opaque) else 0
    lab, parts = ndimage.label(canvas[..., 3] > 0)
    print(f"{dst}: {img.width}x{img.height}, {ncol} colours, {parts} separate part(s)")
    problems = []
    if ncol > 64:
        problems.append("more than 64 colours")
    if img.width % 8 or img.height % 8:
        problems.append("size not a multiple of 8")
    print("sprite standard:", "ok" if not problems else "; ".join(problems))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe")
    d.add_argument("sprites", nargs="+")
    d.add_argument("--glows", action="store_true", help="list every glow colour family instead")
    d.add_argument("--neutral", action="store_true", help="words for things that are not vehicles")
    s = sub.add_parser("sheet")
    s.add_argument("dst")
    s.add_argument("rows", nargs="+", help="sprite folders (orig.png + variants)")
    s.add_argument("--cell", type=int, default=160, help="cell size in px")
    i = sub.add_parser("init")
    i.add_argument("src")
    i.add_argument("dst")
    i.add_argument("--canvas", type=int, default=512)
    i.add_argument(
        "--size", type=int, default=None, help="final size: the sprite is fitted to it first when it differs"
    )
    i.add_argument("--mask", default=None, help="touch-up mask at the final size, white = redo")
    o = sub.add_parser("post")
    o.add_argument("src")
    o.add_argument("dst")
    o.add_argument("--size", type=int, default=128)
    o.add_argument(
        "--min-part", type=float, default=0.004, help="loose parts smaller than this share of the body are dropped"
    )
    o.add_argument(
        "--mirror",
        choices=["none", "left", "right", "auto"],
        default="none",
        help="auto: mirror the left half when --orig is symmetric",
    )
    o.add_argument("--orig", default=None, help="the sprite being redone (for --mirror auto)")
    o.add_argument("--no-tidy", action="store_true", help="no lone-pixel clean-up and no outline")
    o.add_argument("--no-outline", action="store_true", help="keep the lone-pixel clean-up, no dark outline")
    o.add_argument("--key-tol", type=int, default=70, help="how far from the key green a pixel is still background")
    o.add_argument("--max-colours", type=int, default=64, help="the most colours the sprite keeps (2 to 64)")
    o.add_argument("--edge", type=float, default=0.5, help="share of a block that must be body to draw its pixel")
    o.add_argument("--margin", type=int, default=2, help="empty pixels around the sprite (icons: 0)")
    o.add_argument(
        "--keep-green", action="store_true", help="keep green inside the sprite (only the border green is background)"
    )
    o.add_argument("--grid", default=None, help="the init's grid file (<init>.png.json)")
    o.add_argument("--legacy", action="store_true", help="the old fitting instead of the pixel grid")
    o.add_argument("--keep-mask", default=None, help="touch-up: only these pixels (white) change...")
    o.add_argument("--base", default=None, help="...the others stay this sprite")
    o.add_argument("--palette", default=None, help="a palette file (json, hex, gpl) applied to the result")
    o.add_argument(
        "--fill",
        type=int,
        default=0,
        help="50 to 100: the share of the canvas the sprite's longest side takes (0: the render's scale)",
    )
    o.add_argument("--tile", action="store_true", help="a tile: full colour, no background removal, no outline")
    o.add_argument("--keep-edge", type=int, default=0, help="with --tile and --orig: px of the original's edge kept")
    fb = sub.add_parser("flatten-background", help="a render with its key-colour background made uniform")
    fb.add_argument("src")
    fb.add_argument("dst")
    pe = sub.add_parser("palette-extract", help="a palette from sprites")
    pe.add_argument("dst", help="the palette .json")
    pe.add_argument("sprites", nargs="+")
    pe.add_argument("-n", type=int, default=32, help="colours at most")
    pe.add_argument("--label", default="")
    pa = sub.add_parser("palette-apply", help="recolour a sprite with a palette")
    pa.add_argument("src")
    pa.add_argument("dst")
    pa.add_argument("--palette", required=True)
    pt = sub.add_parser(
        "palette-autotune", help="a tuning that makes some sprites look like others (mean light, spread, saturation)"
    )
    pt.add_argument("--sprites", nargs="+", required=True, help="the sprites to tune (a group's, a batch's results)")
    pt.add_argument("--reference", nargs="+", required=True, help="the sprites to look like")
    pw = sub.add_parser("palette-words", help="the prompt's colour words for a palette")
    pw.add_argument("palette")
    pw.add_argument("--neutral", action="store_true")
    px = sub.add_parser("palette-export", help="a palette as .hex, .gpl or .png")
    px.add_argument("palette")
    px.add_argument("dst")
    a = ap.parse_args()
    if a.cmd == "flatten-background":
        flatten_background(a.src, a.dst)
        return 0
    if a.cmd == "palette-extract":
        pal = extract_palette(a.sprites, a.n)
        pal["id"] = re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(os.path.basename(a.dst))[0].lower()).strip("_")
        pal["label"] = a.label or pal["id"]
        with open(a.dst, "w") as fh:
            json.dump(pal, fh, indent=1)
        print(f"{a.dst}: {len(pal['colours'])} colours")
        return 0
    if a.cmd == "palette-apply":
        recolour(a.src, a.dst, load_palette(a.palette))
        print(f"{a.dst}: recoloured with {a.palette}")
        return 0
    if a.cmd == "palette-autotune":
        print(json.dumps(auto_tune(_opaque_pixels(a.sprites), colour_stats(_opaque_pixels(a.reference)))))
        return 0
    if a.cmd == "palette-words":
        print(palette_words(load_palette(a.palette), a.neutral))
        return 0
    if a.cmd == "palette-export":
        data = export_palette(load_palette(a.palette), os.path.splitext(a.dst)[1].lstrip(".").lower())
        with open(a.dst, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)
        return 0
    if a.cmd == "describe":
        print(describe_glows(a.sprites) if a.glows else describe(a.sprites, neutral=a.neutral))
    elif a.cmd == "sheet":
        sheet(a.dst, a.rows, a.cell)
    elif a.cmd == "init":
        prepare_init(a.src, a.dst, canvas=a.canvas, size=a.size, mask=a.mask)
    elif a.cmd == "post":
        post(
            a.src,
            a.dst,
            size=a.size,
            min_part=a.min_part,
            key_tol=a.key_tol,
            colours=a.max_colours,
            outline=not a.no_outline,
            edge=a.edge,
            mirror=a.mirror,
            orig=a.orig,
            tidy=not a.no_tidy,
            margin=a.margin,
            keep_green=a.keep_green,
            grid=a.grid,
            legacy=a.legacy,
            keep_mask=a.keep_mask,
            base=a.base,
            palette=a.palette,
            fill=a.fill,
            tile=a.tile,
            keep_edge=a.keep_edge,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
