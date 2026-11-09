"""Accounts: sign-in, the rules of passwords, each user's own space, sharing, what an administrator sees."""

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, make_sprite

PW = "a long enough password"


def signed_in(web, name, password=PW, role="user", must_change=False):
    """A second browser: its own cookie jar, signed in as `name` (created on the fly)."""
    if name not in web.module.ACCOUNTS.users():
        web.module.ACCOUNTS.create(name, password, role=role, must_change=must_change)
    c = TestClient(web.module.app)
    r = c.post("/api/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def team(client):
    """The administrator (client), and two users with their own browser."""
    return client, signed_in(client, "ann"), signed_in(client, "bob")


def make_model(c, mid="units", trigger="pxunit"):
    r = c.post("/api/models", json={"id": mid, "label": mid.title(), "trigger": trigger})
    assert r.status_code == 200, r.text


# ---- signing in


def test_nothing_is_served_before_signing_in(web):
    assert web.get("/api/status").status_code == 401
    assert web.get("/api/models").status_code == 401
    assert "Sign in" in web.get("/").text
    assert web.get("/api/auth/state").json() == {"setup": True, "user": None}


def test_the_first_administrator_needs_the_setup_code(web):
    bad = web.post("/api/auth/setup", json={"username": "root", "password": ADMIN_PASSWORD, "code": "nope"})
    assert bad.status_code == 403
    code = web.module.ACCOUNTS.setup_code()
    ok = web.post("/api/auth/setup", json={"username": "root", "password": ADMIN_PASSWORD, "code": code})
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    assert web.get("/api/status").status_code == 200
    assert web.module.ACCOUNTS.setup_code() is None  # once only
    again = TestClient(web.module.app).post(
        "/api/auth/setup", json={"username": "evil", "password": ADMIN_PASSWORD, "code": code}
    )
    assert again.status_code in (403, 409)


def test_what_existed_before_accounts_goes_to_the_first_administrator(web, env):
    from pxmodel import ROOTS  # noqa: F401  (the scripts are on the path)

    (env["output"] / "old_run").mkdir()
    (env["output"] / "old_run" / "job.txt").write_text("name=old\n")
    code = web.module.ACCOUNTS.setup_code()
    web.post("/api/auth/setup", json={"username": "root", "password": ADMIN_PASSWORD, "code": code})
    assert web.module.ACCOUNTS.owner("result", "old_run") == "root"


def test_wrong_passwords_lock_the_account_for_a_while(client):
    web = TestClient(client.module.app)
    for _ in range(12):
        r = web.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429
    # even the right password waits
    assert web.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).status_code == 429


def test_the_session_cookie_is_http_only_and_logout_ends_it(client):
    r = TestClient(client.module.app).post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get("/api/status").status_code == 401


def test_passwords_are_stored_hashed_and_sessions_only_as_digests(client):
    state = client.module.STATE_DIR
    assert ADMIN_PASSWORD not in (state / "users.json").read_text()
    token = client.cookies.get("sg_session")
    assert token and token not in (state / "sessions.json").read_text()


# ---- passwords


def test_password_policy(client):
    assert client.post("/api/users", json={"username": "short", "password": "tiny"}).status_code == 400
    assert client.post("/api/users", json={"username": "x", "password": PW}).status_code == 400  # name too short
    assert client.post("/api/users", json={"username": "same", "password": "same"}).status_code == 400
    assert client.post("/api/users", json={"username": "okay", "password": PW}).status_code == 200


def test_a_user_changes_their_own_password_and_other_sessions_end(team, client):
    _, ann, _ = team
    other = TestClient(client.module.app)
    other.post("/api/auth/login", json={"username": "ann", "password": PW})
    assert ann.post("/api/auth/password", json={"current": "wrong", "new": "another long password"}).status_code == 403
    assert ann.post("/api/auth/password", json={"current": PW, "new": "another long password"}).status_code == 200
    assert ann.get("/api/status").status_code == 200  # this browser got a fresh session
    assert other.get("/api/status").status_code == 401  # the others were signed out
    assert (
        TestClient(client.module.app).post("/api/auth/login", json={"username": "ann", "password": PW}).status_code
        == 401
    )


def test_an_administrator_can_force_a_password_change(client):
    client.post("/api/users", json={"username": "cat", "password": PW, "must_change": True})
    cat = TestClient(client.module.app)
    assert cat.post("/api/auth/login", json={"username": "cat", "password": PW}).json()["must_change"] is True
    r = cat.get("/api/models")
    assert r.status_code == 403 and r.json()["must_change"] is True
    assert cat.post("/api/auth/password", json={"current": PW, "new": PW}).status_code == 400  # must differ
    assert cat.post("/api/auth/password", json={"current": PW, "new": "brand new password"}).status_code == 200
    assert cat.get("/api/models").status_code == 200


def test_an_administrator_reset_forces_a_change_and_ends_sessions(team, client):
    _, ann, _ = team
    assert client.patch("/api/users/ann", json={"password": "temporary password"}).status_code == 200
    assert ann.get("/api/status").status_code == 401
    ann2 = TestClient(client.module.app)
    me = ann2.post("/api/auth/login", json={"username": "ann", "password": "temporary password"}).json()
    assert me["must_change"] is True


# ---- users


def test_only_administrators_manage_users_and_settings(team):
    admin, ann, _ = team
    for method, url in (
        ("get", "/api/users"),
        ("get", "/api/settings"),
        ("post", "/api/comfy/stop"),
        ("get", "/api/updates"),
    ):
        assert getattr(ann, method)(url).status_code == 403, url
    assert ann.post("/api/users", json={"username": "eve", "password": PW}).status_code == 403
    assert admin.get("/api/users").status_code == 200
    assert [u["username"] for u in ann.get("/api/users/names").json()] == ["admin", "bob"]


def test_the_last_administrator_cannot_be_removed_or_demoted(client):
    assert client.patch("/api/users/admin", json={"role": "user"}).status_code == 409
    assert client.patch("/api/users/admin", json={"disabled": True}).status_code == 400  # (not one's own)
    assert client.delete("/api/users/admin?transfer_to=admin").status_code == 400


def test_a_disabled_user_is_signed_out_and_cannot_come_back(team, client):
    _, ann, _ = team
    client.patch("/api/users/ann", json={"disabled": True})
    assert ann.get("/api/status").status_code == 401
    assert (
        TestClient(client.module.app).post("/api/auth/login", json={"username": "ann", "password": PW}).status_code
        == 401
    )


def test_removing_a_user_hands_their_resources_over(team, client):
    admin, ann, bob = team
    make_model(ann)
    r = admin.delete("/api/users/ann?transfer_to=bob")
    assert r.status_code == 200 and r.json()["transferred"] == 1
    assert [m["id"] for m in bob.get("/api/models").json()] == ["units"]
    assert ann.get("/api/status").status_code == 401


# ---- each user's own space


def test_models_are_private_to_their_owner_and_visible_to_administrators(team):
    admin, ann, bob = team
    make_model(ann)
    assert [m["id"] for m in ann.get("/api/models").json()] == ["units"]
    assert bob.get("/api/models").json() == []
    assert bob.get("/api/models/units").status_code == 404
    assert bob.put("/api/models/units", json={"label": "mine now"}).status_code == 404
    assert bob.delete("/api/models/units").status_code == 404
    seen = admin.get("/api/models").json()
    assert [(m["id"], m["owner"]) for m in seen] == [("units", "ann")]
    assert admin.get("/api/models/units").json()["owner"] == "ann"


def test_the_catalog_and_projects_show_only_your_own(team):
    admin, ann, bob = team
    ann.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit", "project": "Ann's game"})
    assert [p["name"] for p in bob.get("/api/projects").json()] == []
    assert [p["name"] for p in ann.get("/api/projects").json()] == ["Ann's game"]
    assert bob.get("/api/catalog").json()["models"] == []
    assert bob.get("/api/catalog").json()["settings"] == {}  # folders and addresses are for administrators
    assert "px_output" in admin.get("/api/catalog").json()["settings"]


def test_results_files_are_private_and_the_paths_are_checked(team, env):
    admin, ann, bob = team
    run = env["output"] / "ann_run"
    run.mkdir()
    (run / "job.txt").write_text("name=ann_run kind=small\n")
    make_sprite(run / "ann_run_s1.png")
    web = admin.module
    web.ACCOUNTS.claim("result", "ann_run", "ann")
    assert [r["title"] for r in ann.get("/api/results").json()] == ["ann_run"]
    assert bob.get("/api/results").json() == []
    assert ann.get("/api/file", params={"path": str(run / "ann_run_s1.png")}).status_code == 200
    assert bob.get("/api/file", params={"path": str(run / "ann_run_s1.png")}).status_code == 404
    assert bob.post("/api/results/delete", json={"paths": [str(run)]}).status_code == 404
    assert run.exists()
    assert admin.get("/api/file", params={"path": str(run / "ann_run_s1.png")}).status_code == 200
    # outside the data folders, or in ComfyUI's folders: refused to users
    assert bob.get("/api/file", params={"path": "/etc/passwd"}).status_code == 403
    assert bob.get("/api/file", params={"path": str(env["lora"] / "units" / "model.json")}).status_code in (403, 404)


def test_palettes_are_private_until_shared(team):
    admin, ann, bob = team
    body = {"label": "Warm", "colours": ["#401010", "#ff8080"], "mode": "snap"}
    assert ann.put("/api/palettes/warm", json=body).status_code == 200
    assert [p["id"] for p in ann.get("/api/palettes").json()] == ["warm"]
    assert bob.get("/api/palettes").json() == []
    assert bob.get("/api/palettes/warm").status_code == 404
    clash = bob.put("/api/palettes/warm", json=body)
    assert clash.status_code == 400  # (the id is taken: it does not say whose)
    assert [p["owner"] for p in admin.get("/api/palettes").json()] == ["ann"]


def test_jobs_are_private_but_the_queue_shows_the_gpu_is_busy(team):
    admin, ann, bob = team
    app = admin.module
    job = app.JOBS.submit("test", "Ann's secret job", ["true"], lane="cpu")
    assert job["owner"] == ""  # (submitted by the app itself, no request)
    job["owner"] = app.ACCOUNTS.id_of("ann")
    assert any(j["title"] == "Ann's secret job" for j in ann.get("/api/jobs").json())
    assert not any(j["title"] == "Ann's secret job" for j in bob.get("/api/jobs").json())
    assert bob.get(f"/api/jobs/{job['id']}").status_code == 404
    assert bob.get(f"/api/jobs/{job['id']}/log").status_code == 404
    assert bob.post(f"/api/jobs/{job['id']}/cancel", json={}).status_code == 404
    assert any(j["title"] == "Ann's secret job" for j in admin.get("/api/jobs").json())


def test_picks_are_per_user(team, env):
    admin, ann, bob = team
    run = env["output"] / "shared_run"
    run.mkdir()
    (run / "job.txt").write_text("name=shared_run\n")
    png = make_sprite(run / "shared_run_s1.png")
    admin.module.ACCOUNTS.claim("result", "shared_run", "ann")
    admin.module.ACCOUNTS.share("result", "shared_run", [], True, by="ann")
    ann.post("/api/picks", json={"path": str(png), "picked": True})
    assert list(ann.get("/api/picks").json()) == [str(png)]
    assert bob.get("/api/picks").json() == {}
    bob.post("/api/picks", json={"path": str(png), "picked": True, "as": "mine"})
    assert ann.get("/api/picks").json()[str(png)]["as"] != "mine"
    out = bob.post("/api/picks/export", json={}).json()
    assert admin.module.ACCOUNTS.id_of("bob") in out["folder"]  # a folder named by the id, not the name


# ---- sharing


def test_a_shared_model_can_be_used_but_not_changed(team):
    admin, ann, bob = team
    make_model(ann)
    assert ann.put("/api/acl/model/units", json={"users": ["bob"]}).status_code == 200
    assert [(m["id"], m["access"]) for m in bob.get("/api/models").json()] == [("units", "read")]
    assert bob.get("/api/models/units").json()["access"] == "read"
    refused = bob.put("/api/models/units", json={"label": "mine"})
    assert refused.status_code == 403 and "ask its owner" in refused.json()["error"]
    assert bob.delete("/api/models/units").status_code == 403
    # using it: the request goes on to the next check (here, no LoRA installed) instead of being refused
    group = ann.get("/api/models/units").json()["model"]["groups"][0]["id"]
    r = bob.post("/api/generate", json={"model": "units", "group": group})
    assert r.status_code == 400 and "No LoRA installed" in r.json()["error"]
    # a copy is theirs
    assert bob.post("/api/models", json={"id": "bobs", "label": "Bob's", "copy": "units"}).status_code == 200
    assert [m["id"] for m in ann.get("/api/models").json()] == ["units"]


def test_sharing_with_everyone_and_taking_it_back(team):
    _, ann, bob = team
    make_model(ann)
    ann.put("/api/acl/model/units", json={"all": True})
    assert bob.get("/api/models/units").status_code == 200
    ann.put("/api/acl/model/units", json={"all": False, "users": []})
    assert bob.get("/api/models/units").status_code == 404


def test_only_the_owner_shares_and_only_an_administrator_changes_the_owner(team):
    admin, ann, bob = team
    make_model(ann)
    ann.put("/api/acl/model/units", json={"users": ["bob"]})
    assert bob.get("/api/acl/model/units").status_code == 403
    assert bob.put("/api/acl/model/units", json={"all": True}).status_code == 403
    assert ann.put("/api/acl/model/units", json={"owner": "bob"}).status_code == 403
    assert admin.put("/api/acl/model/units", json={"owner": "bob", "users": ["ann"]}).status_code == 200
    assert admin.get("/api/models/units").json()["owner"] == "bob"
    assert ann.get("/api/models/units").json()["access"] == "read"


def test_sharing_with_someone_who_does_not_exist_is_refused(team):
    _, ann, _ = team
    make_model(ann)
    assert ann.put("/api/acl/model/units", json={"users": ["nobody"]}).status_code == 400
    assert ann.put("/api/acl/model/nothing_here", json={"all": True}).status_code == 404
    assert ann.put("/api/acl/user/units", json={"all": True}).status_code == 404


def test_a_shared_palette_is_used_in_a_batch_but_not_changed(team):
    admin, ann, bob = team
    body = {"label": "Warm", "colours": ["#401010", "#ff8080"], "mode": "snap"}
    ann.put("/api/palettes/warm", json=body)
    ann.put("/api/acl/palette/warm", json={"users": ["bob"]})
    assert bob.get("/api/palettes/warm").json()["access"] == "read"
    assert bob.put("/api/palettes/warm", json=body).status_code == 403
    assert bob.delete("/api/palettes/warm").status_code == 403
    assert admin.get("/api/palettes").json()[0]["shared_with"] == ["bob"]


def test_a_result_folder_name_taken_by_someone_else_is_refused(team):
    admin, _, _ = team
    app = admin.module
    app.ACCOUNTS.claim("result", "batch-mine", "ann")
    (app.setting_path("px_output") / "batch-mine").mkdir()
    token = app.acc.CURRENT.set(app.acc.Who("bob", "user"))
    try:
        with pytest.raises(app.HTTPException) as e:
            app.claim_folder("batch-mine")
        assert e.value.status_code == 400 and "belongs to someone else" in e.value.detail
        app.claim_folder("batch-new")  # a new name is claimed
        assert app.ACCOUNTS.owner("result", "batch-new") == "bob"
    finally:
        app.acc.CURRENT.reset(token)
    app.claim_folder("batch-mine")  # the app itself is trusted


def test_a_comparison_only_uses_loras_of_models_you_may_use(team, env):
    admin, ann, bob = team
    make_model(ann)
    (env["comfy"] / "models" / "loras" / "pxunit.safetensors").write_bytes(b"x")
    r = bob.post("/api/matrix", json={"model": "units", "loras": ["pxunit.safetensors"]})
    assert r.status_code == 404  # the model is not shared with bob: it does not exist for him
    ann.put("/api/acl/model/units", json={"users": ["bob"]})
    assert bob.get("/api/loras").json()["files"][0]["name"] == "pxunit.safetensors"
    assert bob.post("/api/loras/delete", json={"names": ["pxunit.safetensors"]}).status_code == 403
    assert (env["comfy"] / "models" / "loras" / "pxunit.safetensors").exists()


def test_nobody_removes_or_disables_their_own_account(client):
    client.post("/api/users", json={"username": "second", "password": PW, "role": "admin", "must_change": False})
    for url in ("/api/users/admin?transfer_to=second",):
        r = client.delete(url)
        assert r.status_code == 400 and "your own" in r.json()["error"]
    assert client.patch("/api/users/admin", json={"disabled": True}).status_code == 400
    assert client.get("/api/auth/me").status_code == 200


def test_updating_is_for_administrators_and_waits_for_an_idle_gpu(team, monkeypatch):
    admin, ann, _ = team
    app = admin.module
    assert ann.post("/api/updates/apply", json={"target": "comfyui"}).status_code == 403
    assert admin.post("/api/updates/apply", json={"target": "rm -rf"}).status_code == 400
    seen = []
    monkeypatch.setattr(app.JOBS, "submit", lambda *a, **k: seen.append((a, k)) or {"id": "j1", "kind": a[0]})
    r = admin.post("/api/updates/apply", json={"target": "sd-scripts"})
    assert r.status_code == 200 and seen[0][0][0] == "update" and seen[0][0][2][-1] == "sd-scripts"
    app.JOBS.jobs["idle"] = {"status": "running", "lane": "cpu", "kind": "batch", "owner": "ann"}
    assert admin.post("/api/updates/apply", json={"target": "studio"}).status_code == 409  # it restarts: no job at all
    app.JOBS.jobs.pop("idle")
    assert admin.post("/api/updates/apply", json={"target": "studio"}).status_code == 200
    app.JOBS.jobs["busy"] = {"status": "running", "lane": "gpu", "kind": "train", "owner": "ann"}
    assert admin.post("/api/updates/apply", json={"target": "comfyui"}).status_code == 409
    app.JOBS.jobs.pop("busy")


def test_one_broken_result_folder_does_not_hide_the_others(client):
    out = client.module.setting_path("px_output")
    good, bad = out / "good_1", out / "empty_1"
    for d in (good, bad):
        d.mkdir(parents=True)
    (good / "job.txt").write_text("name=good kind=small\n")
    (bad / "job.txt").write_text("")  # an empty job.txt used to fail the whole list
    r = client.get("/api/results")
    assert r.status_code == 200, r.text
    assert {i["path"].rsplit("/", 1)[1] for i in r.json()} >= {"good_1", "empty_1"}


FOREIGN_MODEL_ROUTES = [
    ("get", "/api/models/units"),
    ("put", "/api/models/units"),
    ("get", "/api/models/units/prompt?group=a"),
    ("get", "/api/models/units/export"),
    ("post", "/api/models/units/reset"),
    ("post", "/api/models/units/sprites"),
    ("delete", "/api/models/units/sprites"),
    ("post", "/api/models/units/upload"),
    ("post", "/api/models/units/upload-zip"),
    ("post", "/api/models/units/import"),
    ("get", "/api/models/units/lighting"),
    ("post", "/api/train/units/dataset"),
    ("post", "/api/train/units/start"),
    ("post", "/api/train/units/previews"),
    ("put", "/api/train/units/caption"),
]
ADMIN_ONLY_ROUTES = [
    ("get", "/api/settings"),
    ("put", "/api/settings"),
    ("post", "/api/comfy/stop"),
    ("get", "/api/updates"),
    ("post", "/api/updates/apply"),
    ("post", "/api/train/setup-sdxl"),
    ("get", "/api/users"),
]


@pytest.mark.parametrize("method,url", FOREIGN_MODEL_ROUTES)
def test_nobody_reaches_a_model_that_is_not_theirs_by_any_route(team, method, url):
    admin, ann, bob = team
    make_model(ann)
    r = getattr(bob, method)(url, **({} if method in ("get", "delete") else {"json": {}}))
    assert r.status_code in (403, 404), (method, url, r.status_code, r.text[:200])
    assert r.status_code != 200


@pytest.mark.parametrize("method,url", ADMIN_ONLY_ROUTES)
def test_administrator_only_routes_refuse_users_and_anonymous(team, web, method, url):
    admin, ann, bob = team
    for c, want in ((ann, 403), (TestClient(web.module.app), 401)):
        r = getattr(c, method)(url, **({} if method == "get" else {"json": {}}))
        assert r.status_code == want, (method, url, r.status_code)


def test_a_new_user_with_a_deleted_users_name_starts_with_nothing(team, env):
    admin, ann, bob = team
    mod = admin.module
    make_model(ann, "shared", "pxshared")
    assert ann.put("/api/acl/model/shared", json={"users": ["bob"]}).status_code == 200
    make_model(bob, "bobs", "pxbobs")
    bid = mod.ACCOUNTS.id_of("bob")
    aid = mod.ACCOUNTS.id_of("admin")
    with mod._lock:
        mod.JOBS.jobs["jb"] = {
            "id": "jb", "kind": "generate", "status": "done", "returncode": 0, "error": None, "log": str(env["output"] / "jb.log"),
            "created": 1.0, "started": 1.0, "ended": 2.0, "meta": {}, "title": "bobs job", "owner": bid, "lane": "gpu",
        }  # fmt: skip
        mod._save("picks.json", {"users": {bid: {"/some/path.png": {"as": "x", "time": 1}}}})
        mod._save("projects.json", {"entries": [[bid, "Bobs project"]]})
    (env["output"] / "uploads" / bid).mkdir(parents=True)
    (env["output"] / "uploads" / bid / "sketch.png").write_bytes(b"x")

    assert admin.delete("/api/users/bob").status_code == 200

    again = signed_in(admin, "bob", "another long password")
    assert [m["id"] for m in again.get("/api/models").json()] == []  # not ann's share, not the old model
    assert again.get("/api/jobs").json() == [] or all(j["id"] != "jb" for j in again.get("/api/jobs").json())
    assert again.get("/api/picks").json() == {}
    assert all(p["name"] != "Bobs project" for p in again.get("/api/projects").json())
    assert not (env["output"] / "uploads" / bid).exists()
    # what was theirs went to the administrator, not away
    assert (env["output"] / "uploads" / aid / "from_bob" / "sketch.png").exists()
    assert mod.JOBS.jobs["jb"]["owner"] == aid
    assert mod.ACCOUNTS.id_of("bob") not in (bid,)  # the same name, another identity
    assert not any(bid in str(v) for v in mod.ACCOUNTS._acl().values())
    assert mod.ACCOUNTS.tag("model", "shared")["shared_with"] == []
    assert mod.ACCOUNTS.owner("model", "bobs") == "admin"


def test_the_state_the_app_keeps_is_private_to_its_user(client):
    import stat

    mod = client.module
    mod._save("x.json", {"a": 1})
    for p in (mod.STATE_DIR / "x.json", mod.STATE_DIR / "auth.log"):
        assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600, p
    assert stat.S_IMODE(mod.STATE_DIR.stat().st_mode) == 0o700


def test_the_api_is_not_cached_and_the_schema_is_not_published(client):
    r = client.get("/api/status")
    assert r.headers["cache-control"] == "private, no-store"
    assert client.get("/openapi.json").status_code in (401, 404) or "paths" not in client.get("/openapi.json").text


def test_training_does_not_start_while_comfyui_is_still_running(client, monkeypatch):
    mod = client.module
    monkeypatch.setattr(mod, "comfy_status", lambda: {"up": True})
    monkeypatch.setattr(mod, "comfy_service", lambda action: (True, ""))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="still running"):
        mod.JOBS._free_gpu({"id": "t", "log": "/dev/null"})


def test_an_installation_keyed_by_names_is_migrated_to_ids(team, env):
    """What 0.3.0 and 0.3.1 wrote (names everywhere, no ids) keeps working after the update."""
    import json

    admin, ann, bob = team
    mod = admin.module
    make_model(ann, "units")
    mod.ACCOUNTS.share("model", "units", ["bob"], False)
    state = mod.STATE_DIR
    # turn the state back into what the older versions wrote
    users = json.loads((state / "users.json").read_text())
    for u in users.values():
        u.pop("id", None)
    (state / "users.json").write_text(json.dumps(users))
    sessions = json.loads((state / "sessions.json").read_text())
    for v in sessions.values():
        v.pop("uid", None)  # (sessions of the older versions did not name an id)
    (state / "sessions.json").write_text(json.dumps(sessions))
    (state / "acl.json").write_text(
        json.dumps({"model": {"units": {"owner": "ann", "users": ["bob", "ghost"], "all": False}}})
    )
    (state / "projects.json").write_text(json.dumps({"entries": [["ann", "Game"], ["ghost", "Lost"]]}))
    (state / "picks.json").write_text(json.dumps({"users": {"ann": {"/p.png": {"as": "x", "time": 1}}, "ghost": {}}}))
    with mod._lock:
        mod.JOBS.jobs["old"] = {
            "id": "old", "kind": "generate", "status": "done", "returncode": 0, "error": None, "log": str(state / "old.log"),
            "created": 1.0, "started": 1.0, "ended": 2.0, "meta": {}, "title": "old", "owner": "ann", "lane": "cpu",
        }  # fmt: skip
    (env["output"] / "uploads" / "ann").mkdir(parents=True)
    (env["output"] / "uploads" / "ann" / "s.png").write_bytes(b"x")

    mod.ACCOUNTS._cache.clear()
    mod.ACCOUNTS.migrate_ids()
    mod.migrate_identities()
    mod.migrate_identities()  # again: nothing changes

    aid, bid = mod.ACCOUNTS.id_of("ann"), mod.ACCOUNTS.id_of("bob")
    assert aid.startswith("U-") and bid.startswith("U-") and aid != bid
    assert mod.ACCOUNTS.owner("model", "units") == "ann"
    assert mod.ACCOUNTS.tag("model", "units")["shared_with"] == ["bob"]  # the user who no longer exists is dropped
    assert mod.JOBS.jobs["old"]["owner"] == aid
    assert json.loads((state / "picks.json").read_text())["users"] == {aid: {"/p.png": {"as": "x", "time": 1}}}
    assert json.loads((state / "projects.json").read_text())["entries"] == [[aid, "Game"], ["", "Lost"]]
    assert (env["output"] / "uploads" / aid / "s.png").exists() and not (env["output"] / "uploads" / "ann").exists()
    # and they can still use what is theirs, with the session they had
    assert [m["id"] for m in ann.get("/api/models").json()] == ["units"]
    assert [m["id"] for m in bob.get("/api/models").json()] == ["units"]
    assert any(j["id"] == "old" for j in ann.get("/api/jobs").json())


def test_one_user_cannot_fill_the_gpu_queue_for_everyone(client, monkeypatch):
    import pytest

    app = client.module
    monkeypatch.setattr(app.Jobs, "_gpu_worker", lambda self: None)  # (nothing runs: the jobs just wait)
    monkeypatch.setattr(app, "MAX_QUEUED_PER_USER", 2)
    jobs = app.Jobs()

    def submit_as(owner, lane="gpu"):
        token = app.acc.CURRENT.set(type("W", (), {"id": owner})())
        try:
            return jobs.submit("generate", "t", ["true"], lane=lane)
        finally:
            app.acc.CURRENT.reset(token)

    submit_as("U-ann")
    submit_as("U-ann")
    with pytest.raises(app.HTTPException) as e:
        submit_as("U-ann")
    assert e.value.status_code == 429 and "2 jobs waiting" in e.value.detail
    submit_as("U-bob")  # somebody else can still queue
    assert len(jobs.queue) == 3


def test_a_malformed_sharing_list_is_a_clean_400(team):
    admin, ann, _ = team
    make_model(ann)
    for bad in ([{"a": 1}], [["x"]], [3], "bob"):
        assert ann.put("/api/acl/model/units", json={"users": bad}).status_code == 400
    assert ann.put("/api/acl/model/units", json={"users": [], "owner": ["x"]}).status_code == 400
