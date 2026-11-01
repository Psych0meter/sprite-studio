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
            "groups": [{"id": "red", "words": "red faction", "pattern": "_red_"}, {"id": "blue", "pattern": "_blue_"}],
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
    assert text.startswith("pxunit, red faction, small")
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
