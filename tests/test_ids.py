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
