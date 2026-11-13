"""pxmesh: a top-down sprite seen from a tilted camera."""

from pathlib import Path

import numpy as np
import pytest
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


# --- 3D model ---------------------------------------------------------------------------------------------------


def shape(path, size=64):
    """A sprite with a hole, a thin arm and two parts that touch at a corner (the hard cases for a closed mesh)."""
    yy, xx = np.mgrid[:size, :size]
    c = size / 2
    body = (xx - c) ** 2 + (yy - c) ** 2 < (size * 0.3) ** 2
    hole = (xx - c) ** 2 + (yy - c) ** 2 < (size * 0.1) ** 2
    arm = (abs(yy - c) < 2) & (xx > c)
    diag = np.zeros_like(body)
    diag[2:6, 2:6] = True  # touches the next block at one corner
    diag[6:10, 6:10] = True
    a = np.zeros((size, size, 4), np.uint8)
    on = (body & ~hole) | arm | diag
    a[on] = (150, 90, 60, 255)
    a[..., :3] = np.where(on[..., None], (xx[..., None] * 3 % 200 + 40) * np.array([1, 0.6, 0.4]), 0).astype(np.uint8)
    Image.fromarray(a, "RGBA").save(path)
    return path


def read_glb(path):
    import json
    import struct

    data = Path(path).read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    jlen, jtype = struct.unpack("<II", data[12:20])
    assert jtype == 0x4E4F534A
    doc = json.loads(data[20 : 20 + jlen])
    blen, btype = struct.unpack("<II", data[20 + jlen : 28 + jlen])
    assert btype == 0x004E4942
    blob = data[28 + jlen : 28 + jlen + blen]
    v = doc["bufferViews"][0]
    pos = np.frombuffer(blob[v["byteOffset"] : v["byteOffset"] + v["byteLength"]], "<f4").reshape(-1, 3, 3)
    return doc, pos


def edge_counts(pos):
    """How many times each undirected edge is used, and whether every edge is used once each way (a closed surface)."""
    key = np.round(pos.reshape(-1, 3) * 1e5).astype(np.int64)
    ids = np.unique(key, axis=0, return_inverse=True)[1].ravel().reshape(-1, 3)
    directed = np.concatenate([ids[:, [0, 1]], ids[:, [1, 2]], ids[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, counts = np.unique(undirected, axis=0, return_counts=True)
    _, dcounts = np.unique(directed, axis=0, return_counts=True)
    return counts, dcounts


def test_mesh_is_a_closed_surface_near_the_triangle_budget(tmp_path):
    src = shape(tmp_path / "s.png")
    for want in (300, 1000, 2500):  # a 64 px sprite has only so many pixels: more would not be reached
        n = pxmesh.mesh(src, tmp_path / f"m{want}.glb", size=64, triangles=want)
        doc, pos = read_glb(tmp_path / f"m{want}.glb")
        assert len(pos) == n
        assert want * 0.85 <= n <= want, (want, n)
        counts, directed = edge_counts(pos)
        assert (counts == 2).all()  # every edge shared by exactly two triangles
        assert (directed == 1).all()  # once in each direction: the triangles all face the same way
        a, b, c = pos[:, 0], pos[:, 1], pos[:, 2]
        assert (np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6) > 0  # outward: a positive volume
        assert doc["samplers"][0]["magFilter"] == 9728  # nearest: the palette stays exact


def test_mesh_keeps_the_sprites_palette_in_its_texture(tmp_path):
    import io

    src = shape(tmp_path / "s.png")
    pxmesh.mesh(src, tmp_path / "m.glb", size=64, triangles=1000)
    doc, _ = read_glb(tmp_path / "m.glb")
    data = (tmp_path / "m.glb").read_bytes()
    jlen = int.from_bytes(data[12:16], "little")
    blob = data[28 + jlen :]
    v = doc["bufferViews"][doc["images"][0]["bufferView"]]
    tex = np.array(Image.open(io.BytesIO(blob[v["byteOffset"] : v["byteOffset"] + v["byteLength"]])).convert("RGBA"))
    orig = np.array(Image.open(src).convert("RGBA"))
    solid = orig[..., 3] > 0
    assert (tex[solid] == orig[solid]).all()  # the sprite's own pixels, unchanged
    assert (tex[..., 3] == 255).all()


def test_mesh_triangle_count_is_checked(tmp_path, monkeypatch):
    src = shape(tmp_path / "s.png")
    for bad in (pxmesh.MIN_TRIANGLES - 1, pxmesh.MAX_TRIANGLES + 1):
        with pytest.raises(ValueError, match="triangles must be between"):
            pxmesh.mesh(src, tmp_path / "m.glb", triangles=bad)
    monkeypatch.setattr(pxmesh, "MAX_TRIANGLES", 400)
    with pytest.raises(ValueError):
        pxmesh.mesh(src, tmp_path / "m.glb", triangles=401)
    assert pxmesh.mesh(src, tmp_path / "m.glb", size=64, triangles=400) <= 400
    assert not (tmp_path / "none.glb").exists()


def test_a_small_sprite_stops_at_what_its_pixels_allow(tmp_path):
    src = shape(tmp_path / "s.png", size=32)
    n = pxmesh.mesh(src, tmp_path / "m.glb", size=32, triangles=100000)
    assert 500 < n < 100000  # as fine as the grid goes, not the number asked for
