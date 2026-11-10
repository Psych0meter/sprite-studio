"""Every resource has a stable id; names are labels (ownership, sharing and links follow the id)."""

import json
from pathlib import Path

from test_accounts import team  # noqa: F401  (the administrator and two users)
from test_gallery import lora_file, make_result


def test_ownership_is_kept_by_id_and_follows_a_rename(team, env):  # noqa: F811
    admin, ann, bob = team
    acc = admin.module.ACCOUNTS
    d = make_result(env)
    admin.module.claim_folder(d.name)  # (a job would do this)
    acc.claim("result", d.name, "ann", replace=True)
    uid = acc.uid("result", d.name)
    assert len(uid) == 32 and uid == acc.uid("result", d.name)  # made once, then stable
    state = json.loads((Path(acc.state) / "acl.json").read_text())
    assert uid in state["result"] and d.name not in state["result"]
    assert admin.put(f"/api/acl/result/{d.name}", json={"users": ["bob"]}).status_code == 200
    assert admin.get(f"/api/acl/result/{uid}").json()["shared_with"] == ["bob"]  # the id works as the key too
    acc.rename("result", d.name, "renamed_run")  # the name changes...
    assert acc.owner("result", "renamed_run") == "ann"  # ...the owner and the shares do not
    assert acc.entry("result", "renamed_run")["users"] == [acc.id_of("bob")]
    assert acc.entry("result", d.name) is None


def test_old_name_keyed_lists_get_ids(team, env):  # noqa: F811
    admin, ann, bob = team
    acc = admin.module.ACCOUNTS
    path = Path(acc.state) / "acl.json"
    path.write_text(json.dumps({"palette": {"warm": {"owner": acc.id_of("ann"), "users": [], "all": False}}}))
    acc._cache.clear()
    acc.migrate_ids()
    acc.migrate_ids()  # again: nothing changes
    data = json.loads(path.read_text())
    assert list(data["palette"]) == [acc.uid("palette", "warm", mint=False)] != ["warm"]
    assert acc.owner("palette", "warm") == "ann"


def test_rows_carry_their_id_and_a_renamed_lora_keeps_it(team, env):  # noqa: F811
    admin, ann, bob = team
    lora_file(env)
    make_result(env)
    row = admin.get("/api/loras").json()["files"][0]
    assert len(row["uid"]) == 32
    assert admin.post("/api/loras/rename", json={"name": "pxunit", "to": "other"}).status_code == 200
    row2 = admin.get("/api/loras").json()["files"][0]
    assert row2["name"] == "other.safetensors" and row2["uid"] == row["uid"]


def test_a_project_keeps_its_id_when_renamed(team, env):  # noqa: F811
    admin, ann, bob = team
    admin.post("/api/projects", json={"name": "Alpha"})
    uid = next(p["uid"] for p in admin.get("/api/projects").json() if p["name"] == "Alpha")
    out = admin.post("/api/projects/rename", json={"old": "Alpha", "new": "Beta"}).json()
    assert [p["uid"] for p in out if p["name"] == "Beta"] == [uid]


def test_a_result_can_be_renamed_and_its_links_follow(team, env):  # noqa: F811
    admin, ann, bob = team
    lora_file(env)
    d = make_result(env)
    gid = admin.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()["id"]
    admin.post("/api/picks", json={"path": str(d / "red_small_1_s12.png"), "picked": True})
    uid = admin.module.ACCOUNTS.uid("result", d.name)
    assert admin.post("/api/results/rename", json={"name": d.name, "to": "better_name"}).json() == {
        "renamed": "better_name"
    }
    new = Path(env["output"]) / "better_name"
    assert new.is_dir() and not d.exists()
    assert admin.module.ACCOUNTS.uid("result", "better_name") == uid  # same id
    assert list(admin.get("/api/picks").json()) == [str(new / "red_small_1_s12.png")]
    assert admin.get(f"/api/gallery/{gid}").json()["source"] == str(new / "red_small_1_s11.png")
    assert admin.post("/api/results/rename", json={"name": "better_name", "to": "better_name"}).status_code == 200
    make_result(env, "other")
    assert admin.post("/api/results/rename", json={"name": "better_name", "to": "other"}).status_code == 400
    assert admin.post("/api/results/rename", json={"name": "picked", "to": "x"}).status_code == 404


def test_a_palette_can_be_renamed_and_its_groups_follow(team, env):  # noqa: F811
    from test_accounts import make_model

    admin, ann, bob = team
    make_model(admin)
    assert admin.put("/api/palettes/warm", json={"colours": ["#ff0000"], "label": "warm"}).status_code == 200
    m = admin.get("/api/models/units").json()["model"]
    g = {"id": "red", "label": "Red", "palette": "warm"}
    admin.put("/api/models/units", json={"groups": [*m["groups"], g]})
    uid = admin.module.ACCOUNTS.uid("palette", "warm")
    r = admin.post("/api/palettes/warm/rename", json={"to": "cool"})
    assert r.status_code == 200 and r.json()["id"] == "cool" and r.json()["label"] == "cool"
    assert admin.get("/api/palettes/warm").status_code == 404
    assert admin.module.ACCOUNTS.uid("palette", "cool") == uid
    assert (
        next(x for x in admin.get("/api/models/units").json()["model"]["groups"] if x["id"] == "red")["palette"]
        == "cool"
    )
    assert admin.post("/api/palettes/cool/rename", json={"to": "Bad Name"}).status_code == 400


def test_a_model_can_be_renamed_and_everything_follows(team, env):  # noqa: F811
    from test_accounts import make_model

    admin, ann, bob = team
    make_model(admin)
    lora_file(env)
    d = make_result(env)
    gid = admin.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()["id"]
    uid = admin.module.ACCOUNTS.uid("model", "units")
    r = admin.post("/api/models/units/rename", json={"to": "troops"})
    assert r.status_code == 200 and r.json()["model"]["id"] == "troops"
    assert not (Path(env["lora"]) / "units").exists() and (Path(env["lora"]) / "troops" / "model.json").exists()
    assert admin.get("/api/models/units").status_code == 404
    assert admin.module.ACCOUNTS.uid("model", "troops") == uid
    assert " model=troops " in (d / "job.txt").read_text().splitlines()[0] + " "
    assert admin.get(f"/api/gallery/{gid}").json()["model"] == "troops"
    assert admin.post("/api/models/troops/rename", json={"to": "Not OK"}).status_code == 400
    make_model(admin, "second", "pxsecond")
    assert admin.post("/api/models/troops/rename", json={"to": "second"}).status_code == 400


def test_a_running_batch_shows_its_planned_total(client, env):
    import app as webapp

    d = Path(env["output"]) / "batch-run"
    for n in range(3):  # three sprites started so far: the third is still being drawn
        s = d / "units" / f"s{n}"
        s.mkdir(parents=True)
        (s / "job.txt").write_text("name=s model=units\n")
    job = {"status": "running", "out": str(d), "meta": {"total": 7, "planned": 10}}
    webapp.JOBS.jobs["t1"] = job
    try:
        row = next(i for i in client.get("/api/results").json() if i["type"] == "batch")
        assert row["busy"] and row["planned"] == 10 and row["tried"] == 3
        job["status"] = "done"
        row = next(i for i in client.get("/api/results").json() if i["type"] == "batch")
        assert "planned" not in row  # finished: what was made is the answer
    finally:
        webapp.JOBS.jobs.pop("t1", None)


def test_duplicate_sprites_are_found_and_removed(team, env):  # noqa: F811
    from PIL import Image
    from test_accounts import make_model

    admin, ann, bob = team
    make_model(admin)
    folder = Path(env["lora"]) / "units" / "sprites"
    folder.mkdir(parents=True, exist_ok=True)
    same = Image.new("RGBA", (16, 16), (200, 30, 30, 255))
    for n in ("red_a.png", "red_a_copy.png", "red_a_copy_2.png"):
        same.save(folder / n)
    Image.new("RGBA", (16, 16), (30, 30, 200, 255)).save(folder / "blue.png")
    admin.post("/api/models/units/sprites", json={"names": ["red_a_copy.png"], "words": "by hand"})
    r = admin.post("/api/models/units/duplicates", json={}).json()
    assert (
        r["groups"] == [["red_a_copy.png", "red_a.png", "red_a_copy_2.png"]] and r["removed"] == []
    )  # (the hand-set one is kept)
    assert (folder / "red_a.png").exists()  # listing changes nothing
    r = admin.post("/api/models/units/duplicates", json={"remove": True}).json()
    assert sorted(r["removed"]) == ["red_a.png", "red_a_copy_2.png"]
    assert sorted(p.name for p in folder.glob("*.png")) == ["blue.png", "red_a_copy.png"]
    assert (folder / ".trash" / "red_a.png").exists()
    assert admin.post("/api/models/units/duplicates", json={}).json()["groups"] == []


def test_a_lora_keeps_its_creation_date_when_touched_or_renamed(team, env):  # noqa: F811
    import os
    import time

    admin, ann, bob = team
    f = lora_file(env)
    os.utime(f, (1_000_000_000, 1_000_000_000))
    row = admin.get("/api/loras").json()["files"][0]
    assert row["created"] == 1_000_000_000
    os.utime(f, None)  # (a copy or an edit moves the modification time)
    assert admin.post("/api/loras/rename", json={"name": "pxunit", "to": "moved"}).status_code == 200
    row = admin.get("/api/loras").json()["files"][0]
    assert row["name"] == "moved.safetensors" and row["created"] == 1_000_000_000 and row["mtime"] > time.time() - 60
    assert admin.get("/api/catalog").json()["loras"][0]["created"] == 1_000_000_000


def test_kept_sprites_are_listed_for_the_results_page(team, env):  # noqa: F811
    admin, ann, bob = team
    lora_file(env)
    d = make_result(env)
    assert admin.get("/api/gallery/kept").json() == {"sources": {}}
    gid = admin.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()["id"]
    assert admin.get("/api/gallery/kept").json() == {"sources": {str(d / "red_small_1_s11.png"): gid}}
    admin.post("/api/gallery/delete", json={"ids": [gid]})
    assert admin.get("/api/gallery/kept").json() == {"sources": {}}


def test_gallery_sprites_can_become_training_sprites(team, env):  # noqa: F811
    from test_accounts import make_model

    admin, ann, bob = team
    make_model(admin)
    lora_file(env)
    d = make_result(env)
    gid = admin.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png"), "name": "Nice one"}).json()["id"]
    r = admin.post("/api/picks/to-model", json={"model": "units", "gallery": [gid, "0" * 16]})
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == ["Nice_one.png"] or len(r.json()["saved"]) == 1  # (the unknown id is ignored)
    assert (Path(env["lora"]) / "units" / "sprites" / r.json()["saved"][0]).is_file()
    r = admin.post(
        "/api/picks/to-model", json={"new": {"id": "fresh", "label": "Fresh", "trigger": "pxfresh"}, "gallery": [gid]}
    )
    assert r.status_code == 200 and r.json()["model"] == "fresh" and len(r.json()["saved"]) == 1
    assert admin.post("/api/picks/to-model", json={"model": "units", "gallery": []}).status_code == 400
    # a gallery image you cannot see is not used (and nothing is created for it)
    assert ann.post("/api/picks/to-model", json={"model": "units", "gallery": [gid]}).status_code in (400, 403, 404)
