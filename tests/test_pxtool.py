"""pxtool: tuning, relighting, palettes, the render clean-up."""

import numpy as np
from PIL import Image

import pxtool
from conftest import make_sprite


def opaque(path):
    a = np.array(Image.open(path).convert("RGBA"))
    return a[a[..., 3] > 0][:, :3]


def test_tune_without_settings_changes_nothing():
    px = np.array([[10, 20, 30], [200, 100, 50]], np.uint8)
    assert (pxtool.tune_rgb(px, {}) == px).all()
    assert (pxtool.tune_rgb(px, {"hue": 0, "brightness": 0}) == px).all()


def test_brightness_lifts_the_mean_value():
    px = np.array([[40, 20, 20], [80, 40, 40], [20, 10, 10]], np.uint8)
    before = pxtool.colour_stats(px)["value"]
    after = pxtool.colour_stats(pxtool.tune_rgb(px, {"brightness": 40}))["value"]
    assert after > before


def test_relight_turns_a_sprite_lit_from_below(tmp_path):
    import pxmodel

    src = make_sprite(tmp_path / "s.png", size=48, lit_from_below=True)
    assert pxmodel.light_direction(src)[0] < 0
    a = np.array(Image.open(src).convert("RGBA"))
    out = pxtool.relight(a, 80)
    Image.fromarray(out, "RGBA").save(tmp_path / "r.png")
    assert pxmodel.light_direction(tmp_path / "r.png")[0] > pxmodel.light_direction(src)[0]
    assert (out[..., 3] == a[..., 3]).all()  # the silhouette stays


def test_snap_uses_only_the_palette_colours(sprite):
    pal = {"colours": ["#000000", "#ff0000", "#ffffff"], "mode": "snap", "tune": {}}
    a = np.array(Image.open(sprite).convert("RGBA"))
    out = pxtool.apply_palette(a, pal)
    used = {tuple(c) for c in out[out[..., 3] > 0][:, :3]}
    assert used <= {(0, 0, 0), (255, 0, 0), (255, 255, 255)}


def test_match_spreads_light_like_the_palette(sprite):
    dark = np.array(Image.open(sprite).convert("RGBA"))
    dark[..., :3] //= 4
    pal = {"colours": ["#404040", "#808080", "#c0c0c0"], "weights": [1, 1, 1], "mode": "match", "tune": {}}
    out = pxtool.apply_palette(dark, pal)
    assert out[out[..., 3] > 0][:, :3].mean() > dark[dark[..., 3] > 0][:, :3].mean()


def test_extract_palette_counts_every_opaque_pixel(sprite):
    pal = pxtool.extract_palette([sprite], 8)
    assert 1 <= len(pal["colours"]) <= 8
    assert sum(pal["weights"]) == len(opaque(sprite))


def test_palette_files_round_trip(tmp_path, sprite):
    pal = pxtool.extract_palette([sprite], 6)
    pal["tune"] = {}
    for fmt in ("hex", "gpl"):
        f = tmp_path / f"p.{fmt}"
        f.write_text(pxtool.export_palette(pal, fmt))
        assert pxtool.load_palette(str(f))["colours"] == pal["colours"]


def test_auto_tune_reaches_the_target(tmp_path):
    dark = make_sprite(tmp_path / "dark.png", colour=(60, 30, 30))
    light = make_sprite(tmp_path / "light.png", colour=(220, 180, 180))
    px = pxtool._opaque_pixels([dark])
    target = pxtool.colour_stats(pxtool._opaque_pixels([light]))
    tune = pxtool.auto_tune(px, target)
    after = pxtool.colour_stats(pxtool.tune_rgb(px, tune))
    assert abs(after["value"] - target["value"]) < abs(pxtool.colour_stats(px)["value"] - target["value"])


def render(path, size=512, box=(140, 380, 200, 320)):
    """A fake render: a shaded block on the flat green background."""
    a = np.zeros((size, size, 3), np.uint8)
    a[...] = (0, 255, 0)
    y0, y1, x0, x1 = box
    for y in range(y0, y1):
        a[y, x0:x1] = (120 + (y - y0) // 4, 60, 60)
    Image.fromarray(a).save(path)
    return path


def test_post_removes_the_background_and_fits_the_size(tmp_path):
    src = render(tmp_path / "render.png")
    pxtool.post(str(src), str(tmp_path / "out.png"), size=64, margin=0)
    out = np.array(Image.open(tmp_path / "out.png"))
    assert out.shape[:2] == (64, 64)
    assert out[0, 0, 3] == 0  # the green is gone
    assert (out[..., 3] > 0).sum() > 100


def test_post_fill_sets_the_longest_side(tmp_path):
    src = render(tmp_path / "render.png")
    for fill in (100, 75):
        dst = tmp_path / f"fill{fill}.png"
        pxtool.post(str(src), str(dst), size=64, margin=0, fill=fill)
        a = np.array(Image.open(dst))
        ys, xs = np.nonzero(a[..., 3])
        assert max(ys.max() - ys.min() + 1, xs.max() - xs.min() + 1) == round(64 * fill / 100)
