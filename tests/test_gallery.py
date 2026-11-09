"""The gallery (a user's kept sprites with their recipe), and the LoRA files that must not change or go away."""

import io
import json
import zipfile
from pathlib import Path

from PIL import Image
from test_accounts import team  # noqa: F401  (the administrator and two users)


def make_result(env, name="red_small_1", lora="pxunit.safetensors", seeds=(11, 12)):
    """A finished result folder of one sprite with two variants, as pxmake.sh leaves it."""
    d = Path(env["output"]) / name
    d.mkdir(parents=True)
    (d / "job.txt").write_text(
        f"name={name} model=units group=red kind=small lora={lora} res=512 size=32 init=none palette=none\n"
        "prompt: pxunit, red team, small unit\n"
    )
    for s in seeds:
        Image.new("RGBA", (32, 32), (200, 30, 30, 255)).save(d / f"{name}_s{s}.png")
    (d / ".done").touch()
    return d


def lora_file(env, name="pxunit.safetensors", data=b"weights"):
    f = Path(env["comfy"]) / "models" / "loras" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def test_saving_to_the_gallery_keeps_the_image_and_its_recipe(client, env):
    lora_file(env)
    d = make_result(env)
    r = client.post(
        "/api/gallery", json={"path": str(d / "red_small_1_s11.png"), "folder": "Reds", "tags": ["Fast", "fast", "x y"]}
    )
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["seed"] == "11" and item["lora"] == "pxunit.safetensors" and item["folder"] == "Reds"
    assert item["tags"] == ["fast", "x y"] and item["already"] is False
    detail = client.get(f"/api/gallery/{item['id']}").json()
    assert detail["params"]["group"] == "red" and "red team" in detail["prompt"] and detail["lora_sha"]
    assert client.get(f"/api/gallery/{item['id']}/image").content[:4] == b"\x89PNG"
    again = client.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()
    assert again["id"] == item["id"] and again["already"] is True


def test_a_gallery_image_survives_its_result_and_pins_its_lora(client, env):
    lora = lora_file(env)
    d = make_result(env)
    gid = client.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()["id"]
    assert client.post("/api/results/delete", json={"paths": [str(d)]}).status_code == 200
    assert client.get(f"/api/gallery/{gid}/image").status_code == 200  # the result is gone, the image is not
    r = client.post("/api/loras/delete", json={"names": ["pxunit.safetensors"]})
    assert r.status_code == 409 and "gallery image" in r.text
    assert lora.exists()
    assert client.post("/api/loras/rename", json={"name": "pxunit.safetensors", "to": "pxunit_new"}).status_code == 409
    assert client.post("/api/gallery/delete", json={"ids": [gid]}).json() == {"deleted": 1}
    assert client.post("/api/loras/delete", json={"names": ["pxunit.safetensors"]}).status_code == 200
    assert not lora.exists()


def test_a_lora_with_results_cannot_be_deleted(client, env):
    lora = lora_file(env)
    d = make_result(env)
    r = client.post("/api/loras/delete", json={"names": ["pxunit.safetensors"]})
    assert r.status_code == 409 and lora.exists()
    client.post("/api/results/delete", json={"paths": [str(d)]})
    assert client.post("/api/loras/delete", json={"names": ["pxunit.safetensors"]}).status_code == 200


def test_installing_over_a_lora_keeps_the_old_file(client, env):
    import app as webapp

    old = lora_file(env, data=b"old weights")
    src = Path(env["comfy"]).parent / "new.safetensors"
    src.write_bytes(b"new weights")
    webapp.install_lora(src, "pxunit.safetensors", "units")
    kept = list(old.parent.glob("pxunit_*.safetensors"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"old weights"
    assert old.read_bytes() == b"new weights"


def test_the_gallery_is_per_user_and_shareable(team, env):  # noqa: F811
    admin, ann, bob = team
    lora_file(env)
    d = make_result(env)
    # the result is the administrator's: share it, so ann may keep a sprite of it
    key = d.name
    admin.module.ACCOUNTS.claim("result", key, admin.get("/api/auth/me").json()["username"])
    assert admin.put(f"/api/acl/result/{key}", json={"users": ["ann"]}).status_code == 200
    gid = ann.post("/api/gallery", json={"path": str(d / "red_small_1_s12.png")}).json()["id"]
    assert [i["id"] for i in ann.get("/api/gallery").json()["items"]] == [gid]
    assert bob.get("/api/gallery").json()["items"] == []
    assert bob.get(f"/api/gallery/{gid}/image").status_code == 404
    assert bob.post("/api/gallery/delete", json={"ids": [gid]}).json() == {"deleted": 0}
    assert ann.put(f"/api/acl/gallery/{gid}", json={"users": ["bob"]}).status_code == 200
    assert bob.get(f"/api/gallery/{gid}/image").status_code == 200  # shared: to look at and export
    assert bob.patch(f"/api/gallery/{gid}", json={"name": "mine"}).status_code == 403
    assert bob.post("/api/gallery/delete", json={"ids": [gid]}).json() == {"deleted": 0}
    z = bob.post("/api/gallery/export", json={"ids": [gid]})
    assert z.status_code == 200 and "manifest.json" in zipfile.ZipFile(io.BytesIO(z.content)).namelist()


def test_only_name_folder_tags_and_note_change_and_bulk_moves_work(client, env):
    lora_file(env)
    d = make_result(env)
    a = client.post("/api/gallery", json={"path": str(d / "red_small_1_s11.png")}).json()["id"]
    b = client.post("/api/gallery", json={"path": str(d / "red_small_1_s12.png")}).json()["id"]
    r = client.patch(
        f"/api/gallery/{a}", json={"name": "Hero one", "seed": "999", "lora": "other.safetensors", "tags": ["a"]}
    )
    assert r.json()["name"] == "Hero_one" and r.json()["seed"] == "11" and r.json()["lora"] == "pxunit.safetensors"
    assert client.post(
        "/api/gallery/bulk", json={"ids": [a, b], "folder": "Albums", "add_tags": ["keep"], "remove_tags": ["a"]}
    ).json() == {"changed": 2}
    items = {i["id"]: i for i in client.get("/api/gallery").json()["items"]}
    assert items[a]["folder"] == "Albums" and items[a]["tags"] == ["keep"] and items[b]["tags"] == ["keep"]


def test_export_names_files_after_sprites_and_can_keep_folders(client, env):
    lora_file(env)
    d = make_result(env)
    ids = []
    for s in (11, 12):
        ids.append(
            client.post(
                "/api/gallery", json={"path": str(d / f"red_small_1_s{s}.png"), "name": "same", "folder": "Reds"}
            ).json()["id"]
        )
    z = zipfile.ZipFile(io.BytesIO(client.post("/api/gallery/export", json={"ids": ids}).content))
    assert sorted(n for n in z.namelist() if n.endswith(".png")) == ["same.png", "same_2.png"]
    z = zipfile.ZipFile(io.BytesIO(client.post("/api/gallery/export", json={"ids": ids, "folders": True}).content))
    assert "Reds/same.png" in z.namelist()
    man = json.loads(z.read("manifest.json"))
    assert man[0]["lora"] == "pxunit.safetensors" and man[0]["seed"] in ("11", "12")
    assert client.post("/api/gallery/export", json={"ids": ["nope"]}).status_code == 400


def test_only_generated_sprites_can_be_saved(client, env):
    d = make_result(env)
    assert client.post("/api/gallery", json={"path": str(d / "job.txt")}).status_code == 400
    assert client.post("/api/gallery", json={"path": "/etc/passwd"}).status_code in (400, 403, 404)
