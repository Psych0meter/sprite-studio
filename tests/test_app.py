"""The web app's API, on empty isolated folders (no GPU, no ComfyUI)."""

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
