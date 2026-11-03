#!/usr/bin/env python3
"""pxmodel.py: the sprite models (one LoRA each) and everything derived
from them: training sets, preview prompts, generation prompts, batch lists.

A model lives in $PX_LORA_HOME/<id>/ (default ~/lora/<id>/):

  model.json   what it is (below)
  sprites/     the sprites it learns from (uploaded, or imported from a
               source folder); also what a batch remasters
  dataset/, output/            SD 1.5 training set and training output
  dataset_xl/, output_xl/      the same for SDXL

model.json:

  id, label, trigger        the LoRA's trigger word (pxunit...)
  project                   the project (game) it belongs to
  colour_words              "hard" (metal hull, armour plating) or
                            "neutral" (body, highlights)
  colours_from              where a remaster's colour words come from:
                            "group" (the other sprites of its group: one
                            look per group) or "self" (the sprite itself:
                            every item has its own colours)
  tail                      words every caption ends with ("top-down view,
                            facing up")
  size                      final size of a generated sprite with no kind
  fill                      50..100: share of the canvas a new sprite's
                            longest side takes (0: as drawn); kinds override it
  repeats                   times each image is seen per epoch (0: about
                            300 steps per epoch, whatever the number of sprites)
  relight                   0..100: training images of sprites lit from
                            below get their edges relit from the top
  negative                  words the generator avoids, on top of the usual
                            ones ("lit from below, shadows on top")
  groups   [ {id, label, words, keywords, pattern, name_pattern,
              name_format, enabled, palette} ]
            the main axis: a faction, a family of items... words go in
            every caption of the group ("red faction"), keywords too
            (extra description: "organic insect hull"). pattern: regex on
            the file name that puts a sprite in the group (when not set by
            hand); name_pattern: its first capture is the sprite's own
            words ("acid spitter"), formatted by name_format ("{} state");
            palette: a palette id put on what is generated for the group
  kinds    [ {id, label, words, keywords, negative, size, fill, pattern, denoise} ]
            the second, optional axis: a size class (small 32 px, large
            128 px...). size sets the pixel grid and the final size;
            denoise, how far a remaster goes (default 0.7); keywords: extra
            words for this kind (in captions and prompts); negative: words the
            generator avoids for this kind
  sprites  { "file.png": {group, kind, words, exclude} }
            per-sprite choices made by hand; they win over the patterns
  batch_skip   regex of sprites a batch leaves out (two-state art...)
  previews     the preview prompts of a training run (empty: one per group)
  imports  [ {root, glob, group, prefix, skip, only_grouped} ]
            where "Import" copies sprites from (root: a named source folder)

Caption of a sprite: trigger, group words, kind words, the sprite's words,
group keywords, kind keywords, tail, its colours, "pixel art", "flat green
background".

  pxmodel.py list                         the models
  pxmodel.py get <model> <field>          one field of model.json (trigger...)
  pxmodel.py init-defaults [--set S]      add a template set's models that are missing
  pxmodel.py matrix-subjects <model>      the test subjects of a LoRA comparison
  pxmodel.py import <model>               copy sprites in following its import rules
  pxmodel.py sprites <model>              every sprite and what it is
  pxmodel.py dataset <model> <dst> [--res 512] [--repeats N]
  pxmodel.py previews <model> [--res 512]  sample prompts for sd-scripts
  pxmodel.py preview-plan <model> --out DIR --name N   the preview renders a run is missing (TSV)
  pxmodel.py prompt <model> --group G [--kind K] [--extra TEXT] [--colours TEXT]
                  [--refs-kind K] [--exclude FILE] [--palette P]   prompt, refs and size, as JSON
  pxmodel.py batch-list <model> [--groups a,b] [--kinds a,b]
  pxmodel.py invent-list <model> [--groups a,b] [--kinds a,b]
"""

import argparse
import copy
import csv
import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contextlib

import pxtool  # noqa: E402

GREEN = (0, 255, 0)
HOME = os.environ.get("PX_LORA_HOME", os.path.expanduser("~/lora"))
DATA = os.environ.get("SG_DATA", os.path.expanduser("~/sprites"))
# Named source folders that import rules copy sprites from. "input" and
# "game" are the defaults; PX_SOURCES adds more: '{"tiles": "/mnt/x/tiles"}'.
ROOTS = {
    "input": os.environ.get("PX_INPUT", os.path.join(DATA, "input")),
    "game": os.environ.get("PX_GAME", os.path.join(DATA, "game")),
}
with contextlib.suppress(ValueError):
    ROOTS.update(json.loads(os.environ.get("PX_SOURCES", "") or "{}"))


# Template sets: ready-made models to start a project from. The generic set
# is built in; more sets are JSON files in templates/ next to this script or
# in $PX_LORA_HOME/.templates/ ({"label", "description", "models": {id:
# model}}), for example a model exported from the web app.
GENERIC_MODELS = {
    "topdown_units": {
        "label": "Top-down units",
        "trigger": "pxunit",
        "colour_words": "hard",
        "tail": "top-down view, facing up",
        "size": 64,
        "repeats": 0,
        "groups": [{"id": "main", "label": "Main", "words": "", "pattern": "."}],
        "kinds": [
            {"id": "small", "label": "Small", "words": "small unit", "size": 32, "pattern": r"(^|_)(s|small)(_|$)"},
            {"id": "medium", "label": "Medium", "words": "medium unit", "size": 64, "pattern": r"(^|_)(m|medium)(_|$)"},
            {"id": "large", "label": "Large", "words": "large unit", "size": 128, "pattern": r"(^|_)(l|large)(_|$)"},
        ],
    },
    "side_characters": {
        "label": "Side-view characters",
        "trigger": "pxchar",
        "colour_words": "neutral",
        "colours_from": "self",
        "tail": "side view character, facing right, full body",
        "size": 32,
        "repeats": 0,
        "groups": [{"id": "main", "label": "Main", "words": "", "pattern": ".", "name_pattern": r"^(.+?)(?:_\d+)?$"}],
    },
    "item_icons": {
        "label": "Item icons",
        "trigger": "pxitem",
        "colour_words": "neutral",
        "colours_from": "self",
        "tail": "game item icon",
        "size": 32,
        "repeats": 0,
        "groups": [{"id": "main", "label": "Items", "words": "", "pattern": ".", "name_pattern": r"^(.+?)(?:_\d+)?$"}],
    },
    "props": {
        "label": "Props and obstacles",
        "trigger": "pxprop",
        "colour_words": "neutral",
        "colours_from": "self",
        "tail": "top-down view, game prop",
        "size": 32,
        "repeats": 0,
        "groups": [{"id": "main", "label": "Props", "words": "", "pattern": ".", "name_pattern": r"^(.+?)(?:_\d+)?$"}],
    },
}

TEMPLATE_SETS = {
    "generic": {
        "label": "Generic starting points",
        "description": "Empty models to fill with any game's sprites: top-down units in three sizes, "
        "side-view characters, item icons, props.",
        "models": GENERIC_MODELS,
    },
}


def template_sets():
    """The built-in template sets plus any JSON ones."""
    sets = dict(TEMPLATE_SETS)
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (os.path.join(here, "templates"), os.path.join(HOME, ".templates")):
        for f in sorted(glob.glob(os.path.join(folder, "*.json"))):
            try:
                with open(f) as fh:
                    d = json.load(fh)
                if isinstance(d.get("models"), dict):
                    sets[os.path.basename(f)[:-5]] = d
            except (OSError, ValueError):
                pass
    return sets


def from_template(set_id, model_id, new_id=None, project=None):
    """A new model (not saved) from a template set's model."""
    t = template_sets()[set_id]
    m = normalise(t["models"][model_id], new_id or model_id)
    m["project"] = project or t.get("label", set_id)
    m["sprites"] = {}
    return m


def words(s):
    return s.replace("_", " ").strip()


# ---------------------------------------------------------------- files


def model_dir(mid):
    return os.path.join(HOME, mid)


def sprites_dir(mid):
    return os.path.join(model_dir(mid), "sprites")


def _fill(v):
    """A fill percentage: 0 (none) or 50..100."""
    try:
        n = int(float(v or 0))
    except (TypeError, ValueError):
        return 0
    return 0 if n <= 0 else max(50, min(100, n))


def is_neutral(m):
    """Colour words for things that are not hard-surface vehicles (body, highlights)."""
    return m["colour_words"] == "neutral"


def normalise(m, mid):
    m = copy.deepcopy(m)
    m["id"] = mid
    m.setdefault("label", mid)
    m.setdefault("trigger", "px" + re.sub(r"\W", "", mid))
    m.setdefault("colour_words", "neutral")
    if m["colour_words"] == "ship":  # the first name of "hard"
        m["colour_words"] = "hard"
    m.setdefault("colours_from", "self" if is_neutral(m) else "group")
    m.setdefault("tail", "")
    m.setdefault("size", 64)
    m.setdefault("repeats", 4)
    m.setdefault("groups", [])
    m.setdefault("kinds", [])
    m.setdefault("sprites", {})
    m.setdefault("batch_skip", "")
    m.setdefault("previews", [])
    m.setdefault("negative", "")
    m.setdefault("imports", [])
    m.setdefault("project", "")
    m.setdefault("relight", 0)  # 0..100: training images of sprites lit from below are relit from the top
    m.setdefault("fill", 0)  # 50..100: share of the canvas new sprites fill (0: the render's own scale)
    m["fill"] = _fill(m["fill"])
    for g in m["groups"]:
        g.setdefault("label", g["id"])
        g.setdefault("words", words(g["id"]))
        g.setdefault("keywords", "")
        g.setdefault("pattern", "")
        g.setdefault("name_pattern", g.get("pattern", ""))
        g.setdefault("name_format", "{}")
        g.setdefault("enabled", True)
        g.setdefault("palette", "")  # a palette id: applied to what is generated for the group
    for k in m["kinds"]:
        k.setdefault("label", k["id"])
        k.setdefault("words", words(k["id"]))
        k.setdefault("size", m["size"])
        k.setdefault("fill", 0)  # 0: the model's
        k["fill"] = _fill(k["fill"])
        k.setdefault("pattern", "")
        k.setdefault("denoise", 0.7)
        k.setdefault("keywords", "")
        k.setdefault("negative", "")
    return m


def load(mid):
    path = os.path.join(model_dir(mid), "model.json")
    if not os.path.exists(path):
        raise SystemExit(f"no model {mid} (no {path})")
    with open(path) as fh:
        return normalise(json.load(fh), mid)


def save(m):
    os.makedirs(sprites_dir(m["id"]), exist_ok=True)
    path = os.path.join(model_dir(m["id"]), "model.json")
    with open(path + ".tmp", "w") as fh:
        json.dump(m, fh, indent=1)
    os.replace(path + ".tmp", path)


def list_models():
    """Every saved model, by id."""
    return sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(HOME, "*", "model.json")))


def import_sprites(m, rules=None, overwrite=False):
    """Copies sprites into the model following its import rules (or the
    given ones). Returns the names copied."""
    os.makedirs(sprites_dir(m["id"]), exist_ok=True)
    done = []
    for r in rules or m["imports"]:
        root = ROOTS.get(r.get("root", "game"), r.get("root"))
        for src in sorted(glob.glob(os.path.join(root, r["glob"]))):
            stem = os.path.basename(src)[:-4]
            if r.get("skip") and re.search(r["skip"], stem):
                continue
            name = r.get("prefix", "") + stem + ".png"
            if r.get("only_grouped") and not r.get("group") and not classify(m, name)["group"]:
                continue  # a model for some groups only: the others' sprites stay out
            dst = os.path.join(sprites_dir(m["id"]), name)
            if os.path.exists(dst) and not overwrite:
                continue
            shutil.copy2(src, dst)
            if r.get("group"):
                m["sprites"].setdefault(name, {})["group"] = r["group"]
            done.append(name)
    return done


# ---------------------------------------------------------------- what each sprite is


def classify(m, name):
    """{group, kind, words, exclude, auto} of one sprite file name."""
    stem = name[:-4] if name.endswith(".png") else name
    ov = m["sprites"].get(name, {})
    group = next((g for g in m["groups"] if g["id"] == ov.get("group")), None)
    auto = {}
    if not group:
        group = next((g for g in m["groups"] if g["pattern"] and re.search(g["pattern"], stem)), None)
        auto["group"] = True
    kind = next((k for k in m["kinds"] if k["id"] == ov.get("kind")), None)
    if not kind:
        kind = next((k for k in m["kinds"] if k["pattern"] and re.search(k["pattern"], stem)), None)
        auto["kind"] = True
    if "words" in ov:
        w = ov["words"]
    else:
        w = ""
        if group and group["name_pattern"]:
            mm = re.search(group["name_pattern"], stem)
            if mm and mm.groups() and mm.group(1):
                w = group["name_format"].format(words(mm.group(1)))
    return {
        "name": name,
        "group": group["id"] if group else None,
        "kind": kind["id"] if kind else None,
        "words": w,
        "exclude": bool(ov.get("exclude")),
        "auto": auto,
    }


def sprites(m):
    out = []
    for f in sorted(glob.glob(os.path.join(sprites_dir(m["id"]), "*.png"))):
        c = classify(m, os.path.basename(f))
        im = Image.open(f)
        c.update(path=f, w=im.width, h=im.height)
        g = next((g for g in m["groups"] if g["id"] == c["group"]), None)
        c["trained"] = bool(g and g["enabled"] and not c["exclude"])
        out.append(c)
    return out


def caption(m, group, kind, own_words, colours, extra=""):
    parts = [m["trigger"]]
    if group:
        parts.append(group["words"])
    if kind:
        parts.append(kind["words"])
    for w in (own_words, extra):
        if w:
            parts.append(w)
    if group and group["keywords"]:
        parts.append(group["keywords"])
    if kind and kind.get("keywords"):
        parts.append(kind["keywords"])
    if m["tail"]:
        parts.append(m["tail"])
    if colours:
        parts.append(colours)
    parts += ["pixel art", "flat green background"]
    return ", ".join(p.strip(", ") for p in parts if p.strip(", "))


def describe(m, paths):
    return pxtool.describe(paths, neutral=is_neutral(m)) if paths else ""


def target_size(m, kind, w, h):
    if kind and kind.get("size"):
        return int(kind["size"])
    return max(w, h)


# ---------------------------------------------------------------- training set


def frame(path, res, target, light=0):
    im = Image.open(path).convert("RGBA")
    if light:
        import numpy as np

        im = Image.fromarray(pxtool.relight(np.asarray(im).copy(), light), "RGBA")
    k = min(pxtool.pixel_scale(target, res), pxtool.pixel_scale(max(im.size), res))
    big = im.resize((im.width * k, im.height * k), Image.Resampling.NEAREST)
    out = Image.new("RGBA", (res, res), GREEN + (255,))
    out.alpha_composite(big, ((res - big.width) // 2, (res - big.height) // 2))
    return out.convert("RGB")


def dataset(m, dst, res=512, repeats=0):
    repeats = repeats or m["repeats"]
    if not glob.glob(os.path.join(sprites_dir(m["id"]), "*.png")) and m["imports"]:
        n = len(import_sprites(m))
        save(m)
        print(f"{m['id']}: no sprites yet, {n} imported from the game folders")
    rows = []
    for s in sprites(m):
        if not s["trained"]:
            continue
        g = next(g for g in m["groups"] if g["id"] == s["group"])
        k = next((k for k in m["kinds"] if k["id"] == s["kind"]), None)
        rows.append((s, caption(m, g, k, s["words"], describe(m, [s["path"]])), target_size(m, k, s["w"], s["h"])))
    if not rows:
        raise SystemExit(f"model {m['id']}: no sprite to learn from (add sprites, and put them in an enabled group)")
    if not repeats:
        repeats = max(1, min(30, round(300 / len(rows))))
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    img_dir = os.path.join(dst, "img", f"{repeats}_{m['trigger']}")
    os.makedirs(img_dir)
    thumbs = []
    relit = []
    for s, text, target in rows:
        sid = s["name"][:-4]
        light = m["relight"] if m["relight"] and light_direction(s["path"])[0] < -0.3 else 0
        if light:
            relit.append(sid)
        img = frame(s["path"], res, target, light)
        img.save(os.path.join(img_dir, sid + ".png"))
        with open(os.path.join(img_dir, sid + ".txt"), "w") as fh:
            fh.write(text + "\n")
        thumbs.append(img.resize((128, 128), Image.Resampling.NEAREST))
    with open(os.path.join(dst, "captions.csv"), "w", newline="") as fh:
        csv.writer(fh).writerows([("id", "caption")] + [(s["name"][:-4], t) for s, t, _ in rows])
    cols = 12
    sheet = Image.new("RGB", (cols * 128, ((len(thumbs) + cols - 1) // cols) * 128), (30, 30, 40))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * 128, (i // cols) * 128))
    sheet.save(os.path.join(dst, "sheet.png"))
    left = [s["name"] for s in sprites(m) if not s["trained"]]
    print(f"{m['id']}: {len(rows)} images in {img_dir} ({repeats} repeats = {len(rows) * repeats} steps per epoch)")
    if relit:
        print(
            f"relit from the top (strength {m['relight']}): {len(relit)} sprites lit from below:",
            ", ".join(relit[:12]) + (" ..." if len(relit) > 12 else ""),
        )
    if left:
        more = f" and {len(left) - 12} more" if len(left) > 12 else ""
        print(f"not learned ({len(left)}: no group, group disabled or excluded):", ", ".join(left[:12]) + more)


# ---------------------------------------------------------------- prompts


def palettes_dir():
    return os.path.join(HOME, ".palettes")


def palette_path(ref):
    """A palette id (in palettes_dir) or file; "" or "none": no palette."""
    if not ref or ref == "none":
        return None
    if os.path.isfile(ref):
        return ref
    f = os.path.join(palettes_dir(), ref + ".json")
    if not re.fullmatch(r"[a-z0-9_]{1,60}", ref) or not os.path.isfile(f):
        raise SystemExit(f"no palette {ref} (in {palettes_dir()})")
    return f


def refs_for(m, group_id, kind_id=None, exclude=None):
    """Sprites whose colours name the prompt: the group's, of that kind when
    there are some, else the whole group."""
    ss = [s for s in sprites(m) if s["group"] == group_id and not s["exclude"] and s["path"] != exclude]
    same = [s for s in ss if kind_id and s["kind"] == kind_id]
    return [s["path"] for s in (same or ss)]


def prompt(m, group_id, kind_id=None, extra="", colours="", refs_kind=None, exclude=None, palette=None):
    """palette: None = the group's, "none" = no palette, else an id or a file."""
    g = next((g for g in m["groups"] if g["id"] == group_id), None)
    if not g:
        raise SystemExit(f"model {m['id']} has no group {group_id}")
    k = next((k for k in m["kinds"] if k["id"] == kind_id), None)
    refs = refs_for(m, group_id, refs_kind or kind_id, exclude)
    if exclude and m["colours_from"] == "self":
        refs = [exclude]
    pal_file = palette_path(g.get("palette", "") if palette is None else palette)
    if pal_file and not colours:
        pal = pxtool.load_palette(pal_file)
        if pal.get("words") and pal.get("colours"):
            colours = pxtool.palette_words(pal, neutral=is_neutral(m))
    if not colours:
        colours = describe(m, refs)
    return {
        "palette": pal_file or "",
        "fill": int((k.get("fill") if k else 0) or m.get("fill") or 0),
        "prompt": caption(m, g, k, "", colours, extra),
        "refs": refs,
        "size": int(k["size"]) if k and k.get("size") else int(m["size"]),
        "denoise": float(k["denoise"]) if k else 0.7,
        "negative": negative(m, k),
        "trigger": m["trigger"],
        "neutral": is_neutral(m),
    }


BASE_NEGATIVE = "blurry, 3d render, perspective, side view, text, watermark, soft shadow, gradient"


def negative(m, kind=None):
    extra = [m["negative"], kind.get("negative", "") if kind else ""]
    return ", ".join(x for x in [BASE_NEGATIVE] + [e.strip(" ,") for e in extra] if x)


def previews(m, res=512):
    tail = f"--w {res} --h {res} --s 28 --l 7"
    lines = []
    for p in m["previews"]:
        if not p.strip():
            continue
        # a line may end with its own words to avoid: "... --n organic, creature"
        text, _, own = p.partition(" --n ")
        lines.append((text.strip(), ", ".join(x for x in (negative(m), own.strip(" ,")) if x)))
    if not lines:
        lines = [(p["prompt"], p["negative"]) for p in auto_previews(m)]
    return "\n".join(f"{p} --n {n} {tail} --d {i + 1}" for i, (p, n) in enumerate(lines)) + "\n"


PREVIEW_LINE = re.compile(r"^(.*?) --n (.*?) --w (\d+) --h (\d+) --s (\d+) --l ([\d.]+) --d (\d+)\s*$")
CELL_RE = re.compile(r"_e(\d{6})_(\d{2})_")


def preview_plan(m, out_dir, name, res=512, epochs=None, prompts=2, final_epoch=None):
    """The preview renders a training run is missing, as dicts: one per (checkpoint, preview prompt) with no
    image in out_dir/sample yet. The prompts are the run's own (sample_prompts.txt, else the model's), the
    first `prompts` of them; epochs: only these epochs (a list of ints; None: every checkpoint); final_epoch:
    the epoch the final file (<name>.safetensors, no number) stands for, when no numbered checkpoint does."""
    out_dir = str(out_dir)
    lines = []
    f = os.path.join(out_dir, "sample_prompts.txt")
    text = Path(f).read_text() if os.path.exists(f) else previews(m, res)
    for i, ln in enumerate(x for x in text.splitlines() if x.strip()):
        g = PREVIEW_LINE.match(ln)
        if g:
            p, n, w, _h, steps, cfg, seed = g.groups()
            lines.append((i, p, n, int(steps), float(cfg), int(seed)))
        else:  # a hand-edited line: its text, with the usual settings
            lines.append((i, ln.split(" --")[0], negative(m), 28, 7.0, i + 1))
    ckpts = {}
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        g = re.fullmatch(re.escape(name) + r"-(\d+)\.safetensors", fn)
        if g:
            ckpts[int(g.group(1))] = os.path.join(out_dir, fn)
    final = os.path.join(out_dir, name + ".safetensors")
    if final_epoch and int(final_epoch) not in ckpts and os.path.exists(final):
        ckpts[int(final_epoch)] = final
    have = set()
    sdir = os.path.join(out_dir, "sample")
    for fn in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
        g = CELL_RE.search(fn)
        if g:
            have.add((int(g.group(1)), int(g.group(2))))
    plan = []
    for ep, path in sorted(ckpts.items()):
        if epochs is not None and ep not in epochs:
            continue
        for i, p, n, steps, cfg, seed in lines[: max(0, int(prompts))]:
            if (ep, i) not in have:
                plan.append(
                    {"epoch": ep, "checkpoint": path, "index": i, "prompt": p, "negative": n, "steps": steps,
                     "cfg": cfg, "seed": seed}
                )  # fmt: skip
    return plan


def auto_previews(m, limit=8):
    """One test prompt per enabled group, each with one of the kinds that
    group really has (a different one each time), and no sprite's own
    words: a new design, to see the style rather than copies."""
    ss = [s for s in sprites(m) if s["trained"]]
    out = []
    for i, g in enumerate([g for g in m["groups"] if g["enabled"]][:limit]):
        counts = {k["id"]: sum(1 for s in ss if s["group"] == g["id"] and s["kind"] == k["id"]) for k in m["kinds"]}
        # kinds the group has several sprites of (not a one-off), else any it has
        have = [k for k in m["kinds"] if counts[k["id"]] >= 2] or [k for k in m["kinds"] if counts[k["id"]]]
        k = have[i % len(have)] if have else None
        pr = prompt(m, g["id"], k["id"] if k else None, "" if m["kinds"] else "new design")
        out.append(
            {"group": g["id"], "kind": k["id"] if k else None, "prompt": pr["prompt"], "negative": pr["negative"]}
        )
    return out


def matrix_subjects(m, limit=6):
    """Test subjects for comparing LoRAs: per enabled group (up to limit), a
    new design on its most common kind, and a remaster of one of its sprites.
    Returns (name, pxmake.sh arguments)."""
    ss = [s for s in sprites(m) if s["trained"]]
    out = []
    for g in [g for g in m["groups"] if g["enabled"]][:limit]:
        mine = [s for s in ss if s["group"] == g["id"]]
        kinds = sorted({s["kind"] for s in mine if s["kind"]}, key=lambda k: -sum(1 for s in mine if s["kind"] == k))
        k = kinds[0] if kinds else None
        base = ["--group", g["id"]] + (["--kind", k] if k else [])
        out.append((f"{g['id']}_new", base + ["--extra", "new design"]))
        pick = next((s for s in mine if s["kind"] == k), mine[0] if mine else None)
        if pick:
            kk = ["--kind", pick["kind"]] if pick["kind"] else []
            out.append((f"{g['id']}_remaster", ["--group", g["id"]] + kk + ["--init", pick["path"]]))
    return out


def light_direction(path):
    """How the sprite is lit, from its painted shading: (top, left), each the
    mean brightness step between a pixel's two neighbours, > 0 when the top
    (left) side is brighter. Below about -1: lit from below."""
    import numpy as np

    a = np.asarray(Image.open(path).convert("RGBA")).astype(float)
    al = a[..., 3] > 0
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    v = np.zeros_like(al)
    v[1:-1] = al[:-2] & al[2:] & al[1:-1]
    hz = np.zeros_like(al)
    hz[:, 1:-1] = al[:, :-2] & al[:, 2:] & al[:, 1:-1]
    top = (lum[:-2] - lum[2:])[v[1:-1]]
    left = (lum[:, :-2] - lum[:, 2:])[hz[:, 1:-1]]
    return (float(top.mean()) if top.size else 0.0, float(left.mean()) if left.size else 0.0)


def batch_list(m, groups=None, kinds=None):
    out = []
    for s in sprites(m):
        if not s["group"] or s["exclude"]:
            continue
        if m["batch_skip"] and re.search(m["batch_skip"], s["name"][:-4]):
            continue
        if groups and s["group"] not in groups:
            continue
        if kinds and s["kind"] not in kinds:
            continue
        k = next((k for k in m["kinds"] if k["id"] == s["kind"]), None)
        size = int(k["size"]) if k and k.get("size") else -(-max(s["w"], s["h"]) // 8) * 8
        out.append(
            {
                "name": s["name"],
                "path": s["path"],
                "group": s["group"],
                "kind": s["kind"] or "",
                "size": size,
                "words": s["words"],
            }
        )
    return out


def invent_list(m, groups=None, kinds=None):
    """The (group, kind) pairs new sprites are invented for: every enabled
    group (or the ones given) with every kind it has sprites of (or the kinds
    given, all of them); a model without kinds: one row per group."""
    out = []
    ss = [s for s in sprites(m) if s["group"] and not s["exclude"]]
    for g in m["groups"]:
        if (groups and g["id"] not in groups) or (not groups and not g["enabled"]):
            continue
        if not m["kinds"]:
            out.append({"group": g["id"], "kind": "", "size": int(m["size"])})
            continue
        has = {s["kind"] for s in ss if s["group"] == g["id"]}
        for k in m["kinds"]:
            if (kinds and k["id"] in kinds) or (not kinds and k["id"] in has):
                out.append(
                    {"group": g["id"], "kind": k["id"], "size": int(k["size"]) if k.get("size") else int(m["size"])}
                )
    return out


# ---------------------------------------------------------------- command line


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("get")
    p.add_argument("model")
    p.add_argument("field")
    p = sub.add_parser("init-defaults")
    p.add_argument("--set", default="generic", help="template set (generic, or a JSON set)")
    p = sub.add_parser("matrix-subjects")
    p.add_argument("model")
    p = sub.add_parser("import")
    p.add_argument("model")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("sprites")
    p.add_argument("model")
    p = sub.add_parser("dataset")
    p.add_argument("model")
    p.add_argument("dst")
    p.add_argument("--res", type=int, default=512)
    p.add_argument("--repeats", type=int, default=0)
    p = sub.add_parser("previews")
    p.add_argument("model")
    p.add_argument("--res", type=int, default=512)
    p = sub.add_parser("preview-plan")
    p.add_argument("model")
    p.add_argument("--out", required=True, help="the training output folder")
    p.add_argument("--name", required=True, help="the LoRA's name (pxunit, pxunit_xl)")
    p.add_argument("--res", type=int, default=512)
    p.add_argument("--epochs", default="", help="2,4,6: only these epochs (default: every checkpoint)")
    p.add_argument("--prompts", type=int, default=2, help="the first N preview prompts")
    p.add_argument("--final-epoch", type=int, default=0, help="the epoch the unnumbered final file stands for")
    p = sub.add_parser("prompt")
    p.add_argument("model")
    p.add_argument("--group", required=True)
    p.add_argument("--kind")
    p.add_argument("--extra", default="")
    p.add_argument("--colours", default="")
    p.add_argument("--refs-kind")
    p.add_argument("--exclude")
    p.add_argument("--palette", default=None, help="a palette id or file, none: no palette (default: the group's)")
    p = sub.add_parser("batch-list")
    p.add_argument("model")
    p.add_argument("--groups", default="")
    p.add_argument("--kinds", default="")
    p = sub.add_parser("invent-list")
    p.add_argument("model")
    p.add_argument("--groups", default="")
    p.add_argument("--kinds", default="")
    a = ap.parse_args()

    if a.cmd == "list":
        for mid in list_models():
            m = load(mid)
            n = len(glob.glob(os.path.join(sprites_dir(mid), "*.png")))
            print(f"{mid:12} {m['trigger']:12} {n:4} sprites  {m['label']}")
    elif a.cmd == "get":
        print(load(a.model)[a.field])
    elif a.cmd == "init-defaults":
        sid = a.set
        for mid in template_sets()[sid]["models"]:
            if not os.path.exists(os.path.join(model_dir(mid), "model.json")):
                save(from_template(sid, mid))
                print("created", mid)
    elif a.cmd == "matrix-subjects":
        for name, args in matrix_subjects(load(a.model)):
            print(name + "|" + "\t".join(args))
    elif a.cmd == "import":
        m = load(a.model)
        done = import_sprites(m, overwrite=a.overwrite)
        save(m)
        print(f"{len(done)} sprites imported into {sprites_dir(m['id'])}")
    elif a.cmd == "sprites":
        print(json.dumps(sprites(load(a.model)), indent=1))
    elif a.cmd == "dataset":
        dataset(load(a.model), a.dst, a.res, a.repeats)
    elif a.cmd == "previews":
        sys.stdout.write(previews(load(a.model), a.res))
    elif a.cmd == "preview-plan":
        eps = [int(x) for x in a.epochs.split(",") if x.strip()] or None
        for r in preview_plan(load(a.model), a.out, a.name, a.res, eps, a.prompts, a.final_epoch or None):
            print(
                "\t".join(
                    str(r[k]) for k in ("epoch", "checkpoint", "index", "seed", "steps", "cfg", "prompt", "negative")
                )
            )
    elif a.cmd == "prompt":
        print(json.dumps(prompt(load(a.model), a.group, a.kind, a.extra, a.colours, a.refs_kind, a.exclude, a.palette)))
    elif a.cmd == "batch-list":
        rows = batch_list(load(a.model), [x for x in a.groups.split(",") if x], [x for x in a.kinds.split(",") if x])
        for r in rows:
            print("\t".join(str(r[k] or "-") for k in ("path", "group", "kind", "size")))
    elif a.cmd == "invent-list":
        rows = invent_list(load(a.model), [x for x in a.groups.split(",") if x], [x for x in a.kinds.split(",") if x])
        for r in rows:
            print("\t".join(str(r[k] or "-") for k in ("group", "kind", "size")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
