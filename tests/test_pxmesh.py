"""pxmesh: a top-down sprite seen from a tilted camera."""

import numpy as np
from PIL import Image

import pxmesh
from conftest import make_sprite


def alpha(path):
    return np.array(Image.open(path).convert("RGBA"))[..., 3] > 0


def test_relief_is_zero_outside_and_rounded_inside(sprite):
    rgba = np.array(Image.open(sprite).convert("RGBA"))
    z = pxmesh.relief(rgba, thickness=0.2, detail=0)
    assert (z[rgba[..., 3] < 128] == 0).all()
    inside = z[rgba[..., 3] >= 128]
    assert inside.min() > 0
    # the middle of the body is higher than its edge
    h, w = z.shape
    assert z[h // 2, w // 2] > z[h // 4, w // 2]


def test_turn_by_quarter_turns_moves_the_sprite_and_keeps_the_relief(sprite):
    rgba = np.array(Image.open(sprite).convert("RGBA"))
    rgba[2, 2] = (1, 2, 3, 255)  # a marker in the corner
    z = pxmesh.relief(rgba)
    for yaw, corner in ((90, (2, 29)), (180, (29, 29)), (270, (29, 2))):
        r, zz = pxmesh.turn(rgba, z, yaw)
        assert tuple(r[corner][:3]) == (1, 2, 3)
        assert zz.shape == z.shape
    assert (pxmesh.turn(rgba, z, 0)[0] == rgba).all()
    assert (pxmesh.turn(rgba, z, 360)[0] == rgba).all()


def test_tilt_zero_is_the_top_down_view(tmp_path):
    src = make_sprite(tmp_path / "s.png", size=32)
    rgba = np.array(Image.open(src).convert("RGBA"))
    buf = pxmesh.draw(rgba, pxmesh.relief(rgba), 4, 0)
    assert ((buf[..., 3] > 0) == np.repeat(np.repeat(rgba[..., 3] > 0, 4, 0), 4, 1)).all()
    # the top keeps the sprite's own colours
    assert (buf[buf[..., 3] > 0][:, :3] == np.repeat(np.repeat(rgba[..., :3], 4, 0), 4, 1)[buf[..., 3] > 0]).all()


def test_a_tilted_view_is_flatter_and_shows_sides(tmp_path):
    src = make_sprite(tmp_path / "s.png", size=32, box=(4, 28, 4, 28))
    rgba = np.array(Image.open(src).convert("RGBA"))
    flat = pxmesh.draw(rgba, pxmesh.relief(rgba), 4, 0)
    tilted = pxmesh.draw(rgba, pxmesh.relief(rgba), 4, 50)

    def rows(buf):
        r = np.nonzero((buf[..., 3] > 0).any(axis=1))[0]
        return r.max() - r.min() + 1

    assert rows(tilted) < rows(flat)
    tops = {tuple(c) for c in flat[flat[..., 3] > 0][:, :3]}
    sides = {tuple(c) for c in tilted[tilted[..., 3] > 0][:, :3]} - tops
    assert sides  # darker shades of the sprite's colours that the top-down view does not have


def test_render_makes_a_standard_sprite(tmp_path):
    src = make_sprite(tmp_path / "unit.png", size=64)
    out = pxmesh.render(src, tmp_path / "out.png", size=64, tilt=45, yaw=90)
    im = Image.open(out).convert("RGBA")
    assert im.size == (64, 64)
    a = np.array(im)
    assert (a[..., 3] > 0).any()
    assert set(np.unique(a[..., 3])) <= {0, 255}  # hard edges
    assert len({tuple(c) for c in a[a[..., 3] > 0][:, :3]}) <= 64
    assert (tmp_path / "out_raw.png").exists()  # the render before the clean-up is kept


def test_dataset_names_files_after_sprite_and_yaw(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    make_sprite(src / "unit_a.png", size=32)
    make_sprite(src / "unit_b.png", size=32)
    (src / "notes.txt").write_text("not a sprite")
    made = pxmesh.dataset(str(src), str(tmp_path / "out"), yaws=(0, 180), size=32)
    names = sorted(p.rsplit("/", 1)[-1] for p in made)
    assert names == ["unit_a_yaw0.png", "unit_a_yaw180.png", "unit_b_yaw0.png", "unit_b_yaw180.png"]
    assert all(Image.open(p).size == (32, 32) for p in made)
