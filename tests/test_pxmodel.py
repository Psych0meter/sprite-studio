"""pxmodel: model defaults, captions, prompts, batch and invent lists."""

import json

import pytest

from conftest import make_sprite


def new_model(pxmodel, mid="units", **extra):
    m = pxmodel.normalise(
        {
            "label": "Units",
            "trigger": "pxunit",
            "colour_words": "hard",
            "tail": "top-down view",
            "groups": [{"id": "red", "words": "red team", "pattern": "_red_"}, {"id": "blue", "pattern": "_blue_"}],
            "kinds": [
                {"id": "small", "size": 32, "pattern": "^small_"},
                {"id": "large", "size": 64, "pattern": "^large_"},
            ],
            **extra,
        },
        mid,
    )
    pxmodel.save(m)
    return m


def test_normalise_fills_the_defaults(pxmodel):
    m = pxmodel.normalise({"groups": [{"id": "main"}], "kinds": [{"id": "big", "fill": 30}]}, "x")
    assert m["trigger"] == "pxx"
    assert m["groups"][0]["words"] == "main" and m["groups"][0]["palette"] == ""
    assert m["kinds"][0]["fill"] == 50  # clamped to 50..100
    assert m["project"] == ""


def test_old_colour_word_name_is_migrated(pxmodel):
    assert pxmodel.normalise({"colour_words": "ship"}, "x")["colour_words"] == "hard"


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (None, 0), ("abc", 0), (40, 50), (90, 90), (150, 100)])
def test_fill_is_clamped(pxmodel, value, expected):
    assert pxmodel._fill(value) == expected


def test_sprites_are_classified_by_pattern(pxmodel):
    m = new_model(pxmodel)
    for name in ("small_red_1.png", "large_blue_1.png", "other.png"):
        make_sprite(f"{pxmodel.sprites_dir('units')}/{name}")
    got = {s["name"]: (s["group"], s["kind"]) for s in pxmodel.sprites(m)}
    assert got["small_red_1.png"] == ("red", "small")
    assert got["large_blue_1.png"] == ("blue", "large")
    assert got["other.png"][0] in ("", None)


def test_caption_order(pxmodel):
    m = new_model(pxmodel)
    g, k = m["groups"][0], m["kinds"][0]
    text = pxmodel.caption(m, g, k, "", "dark red body")
    assert text.startswith("pxunit, red team, small")
    assert text.endswith("pixel art, flat green background")
    assert "top-down view" in text and "dark red body" in text


def test_prompt_takes_the_kind_size_and_fill(pxmodel):
    m = new_model(pxmodel)
    m["kinds"][1]["fill"] = 90
    pxmodel.save(m)
    p = pxmodel.prompt(pxmodel.load("units"), "red", "large", colours="grey")
    assert p["size"] == 64 and p["fill"] == 90 and p["palette"] == ""


def test_prompt_uses_the_group_palette_words(pxmodel, env):
    m = new_model(pxmodel)
    (env["lora"] / ".palettes").mkdir()
    (env["lora"] / ".palettes" / "warm.json").write_text(
        json.dumps({"colours": ["#401010", "#c03030", "#ff8080"], "words": True, "mode": "snap"})
    )
    m["groups"][0]["palette"] = "warm"
    pxmodel.save(m)
    p = pxmodel.prompt(pxmodel.load("units"), "red", "small")
    assert p["palette"].endswith("warm.json")
    assert "red" in p["prompt"]


def test_invent_list_follows_the_groups_sprites(pxmodel):
    m = new_model(pxmodel)
    make_sprite(f"{pxmodel.sprites_dir('units')}/small_red_1.png")
    make_sprite(f"{pxmodel.sprites_dir('units')}/large_blue_1.png", size=64)
    rows = {(r["group"], r["kind"]) for r in pxmodel.invent_list(m)}
    assert rows == {("red", "small"), ("blue", "large")}
    rows = {(r["group"], r["kind"]) for r in pxmodel.invent_list(m, ["red"], ["small", "large"])}
    assert rows == {("red", "small"), ("red", "large")}


def test_list_models_only_lists_saved_ones(pxmodel, env):
    new_model(pxmodel, "a")
    (env["lora"] / "not_a_model").mkdir()
    assert pxmodel.list_models() == ["a"]


def _run_dir(tmp_path, *epochs, final=False, samples=()):
    out = tmp_path / "output"
    (out / "sample").mkdir(parents=True)
    for e in epochs:
        (out / f"pxunit-{e:06d}.safetensors").write_bytes(b"x")
    if final:
        (out / "pxunit.safetensors").write_bytes(b"x")
    for e, i in samples:
        (out / "sample" / f"pxunit_e{e:06d}_{i:02d}_20260101000000.png").write_bytes(b"x")
    return out


def test_preview_plan_lists_only_the_missing_renders(pxmodel, tmp_path):
    m = new_model(pxmodel, previews=["pxunit, red unit", "pxunit, blue unit --n organic"])
    out = _run_dir(tmp_path, 2, 4, final=True, samples=[(2, 0)])
    plan = pxmodel.preview_plan(m, out, "pxunit", 512, prompts=2, final_epoch=6)
    assert [(r["epoch"], r["index"]) for r in plan] == [(2, 1), (4, 0), (4, 1), (6, 0), (6, 1)]
    assert plan[-1]["checkpoint"].endswith("pxunit.safetensors")  # the final file stands for epoch 6
    assert "organic" in plan[0]["negative"] and plan[0]["seed"] == 2 and plan[1]["seed"] == 1
    assert [(r["epoch"], r["index"]) for r in pxmodel.preview_plan(m, out, "pxunit", 512, epochs=[4], prompts=1)] == [
        (4, 0)
    ]


def test_preview_plan_does_not_duplicate_a_numbered_final(pxmodel, tmp_path):
    m = new_model(pxmodel, previews=["pxunit, red unit"])
    out = _run_dir(tmp_path, 2, 4, final=True)
    plan = pxmodel.preview_plan(m, out, "pxunit", 512, prompts=1, final_epoch=4)
    assert [r["epoch"] for r in plan] == [2, 4]  # epoch 4 has its numbered twin: the final file is not rendered too


def test_preview_plan_reads_the_runs_own_prompts(pxmodel, tmp_path):
    m = new_model(pxmodel, previews=["pxunit, from the model"])
    out = _run_dir(tmp_path, 2)
    (out / "sample_prompts.txt").write_text("pxunit, from the run --n bad --w 1024 --h 1024 --s 20 --l 5.5 --d 77\n")
    (r,) = pxmodel.preview_plan(m, out, "pxunit", 1024)
    assert (r["prompt"], r["negative"], r["steps"], r["cfg"], r["seed"]) == ("pxunit, from the run", "bad", 20, 5.5, 77)


def test_tile_kinds_and_their_edge_are_normalised_and_reach_the_prompt(pxmodel):
    m = new_model(pxmodel)
    assert m["kinds"][0]["tile"] is False and m["kinds"][0]["edge"] == 0  # old models load unchanged
    m["kinds"][1].update(tile=1, edge=999)
    pxmodel.save(m)
    m = pxmodel.load("units")
    assert m["kinds"][1]["tile"] is True and m["kinds"][1]["edge"] == 64  # clamped
    p = pxmodel.prompt(m, "red", "large", colours="grey")
    assert (p["tile"], p["edge"]) == (1, 64)
    assert pxmodel.prompt(m, "red", "small", colours="grey")["tile"] == 0


def test_a_tile_caption_has_no_pixel_art_or_green_background_words(pxmodel):
    m = new_model(pxmodel)
    g = m["groups"][0]
    plain = pxmodel.caption(m, g, m["kinds"][0], "", "")
    assert plain.endswith("pixel art, flat green background")
    tile = pxmodel.caption(m, g, dict(m["kinds"][0], tile=True), "", "")
    assert tile.endswith("game tile, full colour") and "green" not in tile


def test_the_tiles_template_makes_a_tile_kind(pxmodel):
    m = pxmodel.from_template("generic", "tiles", "floors")
    k = m["kinds"][0]
    assert k["tile"] is True and k["edge"] == 4
    assert "game tile" in pxmodel.caption(m, m["groups"][0], k, "", "")
