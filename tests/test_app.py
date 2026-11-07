"""The web app's API, on empty isolated folders (no GPU, no ComfyUI)."""

import shutil

import numpy as np
from PIL import Image

from conftest import make_sprite


def test_status_reports_the_version(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["version"] not in ("", None)
    assert r.json()["comfy"]["up"] is False


def test_pages_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Sprite Studio" in r.text
    assert client.get("/app.js").status_code == 200


def test_model_life_cycle(client):
    r = client.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit", "project": "My game"})
    assert r.status_code == 200, r.text
    assert [m["id"] for m in client.get("/api/catalog").json()["models"]] == ["units"]
    # trigger words stay unique (they name the LoRA files): a taken one gets a number
    client.post("/api/models", json={"id": "other", "label": "Other", "trigger": "pxunit"})
    assert client.get("/api/models/other").json()["model"]["trigger"] == "pxunit2"
    m = client.get("/api/models/units").json()["model"]
    m["kinds"] = [{"id": "small", "size": 32, "fill": 95}]
    assert client.put("/api/models/units", json={"kinds": m["kinds"]}).status_code == 200
    assert client.get("/api/models/units").json()["model"]["kinds"][0]["fill"] == 95


def test_generation_needs_a_model_and_its_lora(client):
    assert client.post("/api/generate", json={}).status_code == 400
    client.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit"})
    m = client.get("/api/models/units").json()["model"]
    r = client.post("/api/generate", json={"model": "units", "group": m["groups"][0]["id"]})
    assert r.status_code == 400 and "No LoRA installed" in r.json()["error"]


def test_projects(client):
    client.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit", "project": "First"})
    assert client.post("/api/projects", json={"name": "Second"}).status_code == 200
    names = [p["name"] for p in client.get("/api/projects").json()]
    assert names == ["First", "Second"]
    client.post("/api/projects/rename", json={"old": "First", "new": "Renamed"})
    assert client.get("/api/models/units").json()["model"]["project"] == "Renamed"
    client.post("/api/projects/delete", json={"name": "Renamed", "move_to": "Second"})
    assert client.get("/api/models/units").json()["model"]["project"] == "Second"


def test_palettes(client, env):
    r = client.put("/api/palettes/warm", json={"label": "Warm", "colours": ["#401010", "#ff8080"], "mode": "match"})
    assert r.status_code == 200, r.text
    # Snap and Match need colours
    assert client.put("/api/palettes/empty", json={"colours": [], "mode": "snap"}).status_code == 400
    assert client.get("/api/palettes/warm/export?format=hex").text.split() == ["401010", "ff8080"]
    run = env["output"] / "run_1"
    run.mkdir()
    src = make_sprite(run / "run_1_s1.png")
    r = client.post("/api/palettes/apply", json={"id": "warm", "paths": [str(src)]})
    assert r.status_code == 200, r.text
    out = np.array(Image.open(r.json()["files"][0]).convert("RGBA"))
    used = {tuple(c) for c in out[out[..., 3] > 0][:, :3]}
    assert used <= {(0x40, 0x10, 0x10), (0xFF, 0x80, 0x80)}


def test_results_delete_and_restore(client, env):
    job = env["output"] / "run_1"
    job.mkdir()
    (job / "job.txt").write_text("name=run_1 model=units group=red\nprompt: x\n")
    Image.fromarray(np.zeros((8, 8, 4), np.uint8), "RGBA").save(job / "run_1_s1.png")
    assert [i["title"] for i in client.get("/api/results").json()] == ["run_1"]
    assert client.post("/api/results/delete", json={"paths": [str(job)]}).status_code == 200
    assert client.get("/api/results").json() == []
    assert client.post("/api/results/bin/restore", json={"count": 1}).json()["restored"] == ["run_1"]
    assert job.is_dir()


def _cleanup_job(env, name="run_1"):
    """A result folder as pxmake.sh leaves it: job.txt, a raw render and its cleaned variant."""
    from test_pxtool import render

    job = env["output"] / name
    (job / "raw").mkdir(parents=True)
    render(job / "raw" / "run_s7_00001.png")
    (job / "raw" / "run_s7_00001.json").write_text('{"seconds": 3.5}')
    (job / "job.txt").write_text("name=run model=m group=g kind=none size=64 palette=none fill=0\nprompt: a sprite\n")
    from PIL import Image

    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(job / "run_s7.png")
    return job


def test_cleanup_previews_then_saves_a_new_variant(client, env):
    job = _cleanup_job(env)
    body = {"path": str(job / "run_s7.png"), "colours": 4, "edge": 0.4, "outline": False}
    r = client.post("/api/results/cleanup", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["image"].startswith("data:image/png;base64,")
    assert not (job / "run_s7_c1.png").exists()  # a preview writes nothing
    saved = client.post("/api/results/cleanup", json=body | {"save": True}).json()
    assert saved["file"].endswith("run_s7_c1.png")
    again = client.post("/api/results/cleanup", json=body | {"path": saved["file"], "save": True}).json()
    assert again["file"].endswith("run_s7_c2.png")  # cleaning a clean-up numbers on, never overwrites
    detail = client.get("/api/results/detail", params={"path": str(job)}).json()
    assert all(v["raw"] for v in detail["variants"])
    assert all(
        v["seconds"] == 3.5 for v in detail["variants"]
    )  # the render time, read from pxcomfy's .json  # the new variants keep their link to the raw render


def test_cleanup_refuses_what_has_no_raw_render(client, env):
    job = _cleanup_job(env)
    from PIL import Image

    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(job / "plain.png")
    assert client.post("/api/results/cleanup", json={"path": str(job / "plain.png")}).status_code == 400
    assert client.post("/api/results/cleanup", json={"path": "/etc/passwd"}).status_code in (400, 403)


def test_previews_endpoint_queues_a_job_and_reports_what_is_missing(client, env, monkeypatch):
    import app as webapp

    assert client.post("/api/models", json={"id": "units", "label": "Units"}).status_code == 200
    out = env["lora"] / "units" / "output"
    out.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(webapp.JOBS, "submit", lambda *a, **kw: calls.append((a, kw)) or {"id": "j1"})
    trigger = client.get("/api/models/units").json()["model"]["trigger"]
    (out / f"{trigger}-000002.safetensors").write_bytes(b"x")
    r = client.post("/api/train/units/previews", json={"model": "sd15", "epochs": [2], "prompts": 1})
    assert r.status_code == 200, r.text
    (args, kw) = calls[0]
    assert args[0] == "previews" and kw["env"]["EPOCHS"] == "2" and kw["env"]["PROMPTS"] == "1"
    assert kw["env"]["FINAL_EPOCH"] == "12"  # no training run on record: the script's default for SD 1.5
    info = client.get("/api/train/units").json()
    assert info["previews_todo"] >= 1 and 2 in info["previews_epochs"]


def test_a_finished_training_queues_its_previews_when_asked(client, monkeypatch):
    import app as webapp

    seen = []
    monkeypatch.setattr(webapp, "submit_previews", lambda cat, model, *a: seen.append((cat, model)))
    job = {"kind": "train", "status": "done", "meta": {"category": "units", "model": "sdxl", "then_previews": True}}
    webapp.follow_up(job)
    webapp.follow_up(job | {"status": "failed"})
    webapp.follow_up(job | {"meta": {"category": "units", "model": "sdxl"}})
    assert seen == [("units", "sdxl")]


def test_model_prompt_endpoint_builds_a_prompt_for_a_group(client, env):
    assert client.post("/api/models", json={"id": "units", "label": "Units"}).status_code == 200
    m = client.get("/api/models/units").json()["model"]
    r = client.get("/api/models/units/prompt", params={"group": m["groups"][0]["id"]})
    assert r.status_code == 200 and r.json()["prompt"].startswith(m["trigger"])
    assert client.get("/api/models/units/prompt", params={"group": "nope"}).status_code == 400


def _lora_setup(client, env):
    client.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit"})
    out = env["lora"] / "units" / "output"
    out.mkdir(parents=True)
    (out / "pxunit-000004.safetensors").write_bytes(b"four")
    (out / "pxunit.safetensors").write_bytes(b"last!")
    return out


def test_lora_files_are_listed_with_their_origin(client, env):
    out = _lora_setup(client, env)
    client.post("/api/train/units/install", json={"checkpoint": str(out / "pxunit-000004.safetensors")})
    client.post("/api/train/units/install", json={"checkpoint": str(out / "pxunit.safetensors"), "as": "pxunit_final"})
    (env["comfy"] / "models" / "loras" / "pxunit_e8.safetensors").write_bytes(b"copied")  # outside the app
    (env["comfy"] / "models" / "loras" / "other.safetensors").write_bytes(b"?")
    job = env["output"] / "run_1"
    job.mkdir()
    (job / "job.txt").write_text("name=a model=units lora=pxunit.safetensors res=512\n")
    data = client.get("/api/loras").json()
    rows = {r["name"]: r for r in data["files"]}
    assert data["dir_ok"] and set(rows) == {
        "pxunit.safetensors",
        "pxunit_final.safetensors",
        "pxunit_e8.safetensors",
        "other.safetensors",
    }
    d = rows["pxunit.safetensors"]
    assert (d["model"], d["epoch"], d["in_use"], d["known"], d["results"]) == ("units", 4, "SD 1.5", True, 1)
    assert rows["pxunit_final.safetensors"]["final"] and rows["pxunit_final.safetensors"]["epoch"] is None
    assert (
        rows["pxunit_e8.safetensors"]["model"],
        rows["pxunit_e8.safetensors"]["epoch"],
        rows["pxunit_e8.safetensors"]["known"],
    ) == ("units", 8, False)
    assert rows["other.safetensors"]["model"] == "" and rows["other.safetensors"]["in_use"] == ""


def test_lora_use_rename_delete(client, env):
    out = _lora_setup(client, env)
    loras = env["comfy"] / "models" / "loras"
    client.post(
        "/api/train/units/install", json={"checkpoint": str(out / "pxunit-000004.safetensors"), "as": "pxunit_e4"}
    )
    # Use: the file becomes the model's own, keeping where it came from
    assert client.post("/api/loras/use", json={"name": "pxunit_e4"}).json() == {"installed": "pxunit.safetensors"}
    assert (loras / "pxunit.safetensors").read_bytes() == b"four"
    rows = {r["name"]: r for r in client.get("/api/loras").json()["files"]}
    assert rows["pxunit.safetensors"]["epoch"] == 4 and rows["pxunit.safetensors"]["in_use"] == "SD 1.5"
    assert client.post("/api/loras/use", json={"name": "pxunit"}).status_code == 400  # already the one in use
    # rename: not the one in use, not onto a model's own name, not over a file
    assert client.post("/api/loras/rename", json={"name": "pxunit", "to": "x"}).status_code == 400
    assert client.post("/api/loras/rename", json={"name": "pxunit_e4", "to": "pxunit_xl"}).status_code == 400
    (loras / "pxunit_b.safetensors").write_bytes(b"b")
    assert client.post("/api/loras/rename", json={"name": "pxunit_e4", "to": "pxunit_b"}).status_code == 400
    assert client.post("/api/loras/rename", json={"name": "pxunit_e4", "to": "pxunit_best"}).json() == {
        "renamed": "pxunit_best.safetensors"
    }
    rows = {r["name"]: r for r in client.get("/api/loras").json()["files"]}
    assert (
        rows["pxunit_best.safetensors"]["epoch"] == 4 and rows["pxunit_best.safetensors"]["known"]
    )  # the record followed
    # delete, and a missing file is a clear refusal
    assert client.post("/api/loras/delete", json={"names": ["pxunit_best", "pxunit_b"]}).json()["deleted"] == [
        "pxunit_best.safetensors",
        "pxunit_b.safetensors",
    ]
    assert not (loras / "pxunit_best.safetensors").exists()
    assert client.post("/api/loras/rename", json={"name": "pxunit_best", "to": "y"}).status_code == 404
    assert client.post("/api/loras/delete", json={"names": []}).status_code == 400


def test_lora_files_with_odd_names_can_be_renamed_and_deleted(client, env):
    loras = env["comfy"] / "models" / "loras"
    (loras / "my lora v1.safetensors").write_bytes(b"x")
    (loras / "second one.safetensors").write_bytes(b"x")
    assert [r["name"] for r in client.get("/api/loras").json()["files"]] == [
        "my lora v1.safetensors",
        "second one.safetensors",
    ]
    assert client.post("/api/loras/rename", json={"name": "my lora v1.safetensors", "to": "mine"}).json() == {
        "renamed": "mine.safetensors"
    }
    assert client.post("/api/loras/delete", json={"names": ["second one.safetensors"]}).json() == {
        "deleted": ["second one.safetensors"]
    }
    assert client.post("/api/loras/delete", json={"names": ["../x", "gone"]}).json() == {
        "deleted": []
    }  # nothing outside the folder
    assert [r["name"] for r in client.get("/api/loras").json()["files"]] == ["mine.safetensors"]


def test_unnamed_batches_get_their_own_folder_name(client):
    import datetime as dt
    import sys

    app = sys.modules["app"]
    day = f"{dt.date.today():%Y%m%d}"
    m = {"id": "units"}
    assert app.default_batch_tag(m, {}) == f"units-default-{day}"
    assert app.default_batch_tag(m, {"lora": "pxunit_e6.safetensors"}) == f"units-pxunit_e6-{day}"
    assert app.default_batch_tag(m, {"lora": "pxunit_e6", "mode": "invent"}) == f"units-pxunit_e6-{day}-invent"
    assert app.default_batch_tag(m, {"lora": "pxunit_e6"}) != app.default_batch_tag(m, {"lora": "pxunit_e8"})


def test_cleanup_refuses_a_tile(client, env):
    job = _cleanup_job(env)
    txt = (job / "job.txt").read_text()
    (job / "job.txt").write_text(txt.replace("\n", " tile=1 edge=8\n", 1))
    r = client.post("/api/results/cleanup", json={"path": str(job / "run_s7.png")})
    assert r.status_code == 400 and "tile" in r.text


def test_bin_listing_restore_and_erase_by_id(client, env):
    for n in ("run_1", "run_2"):
        job = env["output"] / n
        job.mkdir()
        (job / "job.txt").write_text(f"name={n} model=units group=red\nprompt: x\n")
        Image.fromarray(np.zeros((8, 8, 4), np.uint8), "RGBA").save(job / f"{n}_s1.png")
        client.post("/api/results/delete", json={"paths": [str(job)]})
    info = client.get("/api/results/bin?items=1").json()
    assert info["count"] == 2 and [i["name"] for i in info["items"]] == ["run_2", "run_1"]
    assert all(i["restorable"] and i["cover"] for i in info["items"])
    assert "items" not in client.get("/api/results/bin").json()
    first, second = info["items"][1]["id"], info["items"][0]["id"]
    assert client.post("/api/results/bin/restore", json={"ids": [first]}).json()["restored"] == ["run_1"]
    assert (env["output"] / "run_1").is_dir() and not (env["output"] / "run_2").exists()
    assert client.post("/api/results/bin/empty", json={"ids": [second]}).json()["count"] == 0
    assert client.post("/api/results/bin/empty", json={"ids": ["../x"]}).status_code == 200


def test_jobs_clear_only_successful(client):
    import app as webapp

    with webapp._lock:
        for jid, status in (("a", "done"), ("b", "failed"), ("c", "running")):
            webapp.JOBS.jobs[jid] = {"id": jid, "status": status, "log": "/nonexistent", "created": 1.0}
    try:
        assert client.delete("/api/jobs?only=done").status_code == 200
        assert sorted(webapp.JOBS.jobs) == ["b", "c"]
        assert client.delete("/api/jobs").status_code == 200
        assert sorted(webapp.JOBS.jobs) == ["c"]
    finally:
        webapp.JOBS.jobs.clear()


def test_updates_compares_with_upstream(client, env, tmp_path):
    import subprocess

    def git(cwd, *a):
        subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True
        )

    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "f").write_text("1")
    git(up, "add", "f")
    git(up, "commit", "-q", "-m", "first")
    # the ComfyUI folder itself is the checkout
    shutil.rmtree(env["comfy"])
    subprocess.run(["git", "clone", "-q", str(up), str(env["comfy"])], check=True, capture_output=True)
    (up / "f").write_text("2")
    git(up, "commit", "-qam", "second")
    items = {i["name"]: i for i in client.get("/api/updates?force=1").json()["items"]}
    assert items["ComfyUI"]["ok"] and items["ComfyUI"]["behind"] == 1
    assert items["ComfyUI"]["commits"][0].endswith("second")
    assert not items["kohya sd-scripts"]["ok"]


def test_speed_ignores_jobs_that_did_not_really_finish(client):
    import app as webapp

    def job(jid, status, rc, error, minutes):
        return {
            "id": jid,
            "kind": "train",
            "status": status,
            "returncode": rc,
            "error": error,
            "log": "/x",
            "created": float(len(jid)),
            "started": 1.0,
            "ended": 1.0 + minutes * 60.0,
            "meta": {"model": "sd15"},
        }

    with webapp._lock:
        webapp.JOBS.jobs.clear()
        webapp.JOBS.jobs["ok"] = job("ok", "done", 0, None, 30)
        webapp.JOBS.jobs["cancel"] = job("cancel", "cancelled", -15, None, 2)
        webapp.JOBS.jobs["fail"] = job("fail", "failed", 1, "boom", 1)
        webapp.JOBS.jobs["part"] = job("part!", "done", 4, "2 failed", 3)
    try:
        assert webapp.speed()["train_minutes"] == {"sd15": 30}
    finally:
        webapp.JOBS.jobs.clear()


def test_picks_become_training_sprites(client, env):
    src = env["output"] / "run_1"
    src.mkdir()
    a = make_sprite(src / "a.png")
    b = make_sprite(src / "b.png")
    assert (
        client.post("/api/picks/to-model", json={"new": {"id": "boss", "label": "Boss"}}).status_code == 400
    )  # none yet
    for path, name in ((a, "ship"), (b, "ship")):
        client.post("/api/picks", json={"path": str(path), "picked": True, "as": name})
    r = client.post(
        "/api/picks/to-model", json={"new": {"id": "boss", "label": "Boss ships"}, "group": "main", "unpick": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "boss" and r.json()["saved"] == [
        "ship.png",
        "ship_2.png",
    ]  # one pick name twice: the second is numbered
    assert client.get("/api/picks").json() == {}
    d = client.get("/api/models/boss").json()
    assert sorted(s["name"] for s in d["sprites"]) == ["ship.png", "ship_2.png"] and d["sprites"][0]["group"] == "main"
    # a second round into the existing model never overwrites a sprite
    client.post("/api/picks", json={"path": str(a), "picked": True, "as": "ship"})
    r = client.post("/api/picks/to-model", json={"model": "boss", "kind": "nope"})
    assert r.status_code == 400
    r = client.post("/api/picks/to-model", json={"model": "boss"})
    assert r.json()["saved"] == ["ship_3.png"]
