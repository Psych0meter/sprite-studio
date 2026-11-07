"""What the web app refuses: cross-site requests, oversized or malformed uploads, files that are not images."""

import base64
import io
import zipfile

import pytest
from PIL import Image


def png_url(size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGBA", size, (200, 50, 50, 255)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def zip_url(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return "data:application/zip;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def model(client):
    assert client.post("/api/models", json={"id": "units", "label": "Units", "trigger": "pxunit"}).status_code == 200
    return "units"


def test_a_cross_site_post_is_refused_but_the_same_site_works(client):
    body = {"id": "x", "label": "X", "trigger": "pxx"}
    r = client.post("/api/models", json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "cross-site" in r.text
    r = client.post("/api/models", json=body, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/models", json=body, headers={"Origin": "http://testserver"})
    assert r.status_code == 200, r.text
    assert client.get("/api/catalog", headers={"Origin": "http://evil.example"}).status_code == 200  # reads are fine


def test_an_oversized_request_is_refused(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "MAX_BODY", 1000)
    r = client.post("/api/upload", json={"data": "x" * 5000})
    assert r.status_code == 413


def test_uploads_must_be_real_images_in_strict_base64(client, model):
    bad_b64 = {"data": "data:image/png;base64,@@@not base64@@@"}
    assert client.post("/api/upload", json=bad_b64).status_code == 400
    not_png = {"data": "data:image/png;base64," + base64.b64encode(b"just text, not a PNG").decode()}
    assert client.post("/api/upload", json=not_png).status_code == 400
    assert client.post("/api/upload", json={"data": png_url()}).status_code == 200
    r = client.post(
        f"/api/models/{model}/upload",
        json={"files": [{"name": "a.png", "data": png_url()}, {"name": "b.png", "data": not_png["data"]}]},
    )
    assert r.json()["saved"] == ["a.png"] and "b.png" in r.json()["skipped"][0]


def test_an_image_with_too_many_pixels_is_refused(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "MAX_PIXELS", 50)
    assert client.post("/api/upload", json={"data": png_url((10, 10))}).status_code == 400


def test_zip_limits_and_invalid_entries(client, model, monkeypatch):
    import app

    ok = client.post(
        f"/api/models/{model}/upload-zip",
        json={"data": zip_url({"a/one.png": base64.b64decode(png_url().split(",")[1])})},
    )
    assert ok.json()["saved"] == ["one.png"]
    fake = client.post(f"/api/models/{model}/upload-zip", json={"data": zip_url({"two.png": b"not an image"})})
    assert fake.json()["saved"] == [] and "not a valid PNG" in fake.json()["skipped"][0]
    monkeypatch.setattr(app, "MAX_ZIP_ENTRIES", 2)
    many = client.post(f"/api/models/{model}/upload-zip", json={"data": zip_url({f"{i}.png": b"x" for i in range(3)})})
    assert many.status_code == 400 and "too many" in many.text
    monkeypatch.setattr(app, "MAX_ZIP_ENTRIES", 5000)
    monkeypatch.setattr(app, "MAX_ZIP_TOTAL", 10)
    big = client.post(f"/api/models/{model}/upload-zip", json={"data": zip_url({"a.png": b"x" * 100})})
    assert big.status_code == 400 and "too big" in big.text


def test_only_images_and_small_text_files_are_served(client, env):
    (env["lora"] / "m").mkdir()
    (env["lora"] / "m" / "w.safetensors").write_bytes(b"weights")
    Image.new("RGB", (4, 4)).save(env["lora"] / "m" / "p.png")
    assert client.get("/api/file", params={"path": str(env["lora"] / "m" / "p.png")}).status_code == 200
    assert client.get("/api/file", params={"path": str(env["lora"] / "m" / "w.safetensors")}).status_code == 403
    assert client.get("/api/file", params={"path": "/etc/passwd"}).status_code == 403


def test_a_malformed_image_gives_a_400_not_a_crash(client, env):
    bad = env["lora"] / "bad.png"
    bad.write_bytes(b"not a png")
    assert client.get("/api/info", params={"path": str(bad)}).status_code == 400


def test_too_many_waiting_jobs_are_refused(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "MAX_QUEUED", 0)
    with pytest.raises(app.HTTPException) as e:
        app.JOBS.submit("test", "t", ["true"])
    assert e.value.status_code == 429


def test_a_password_with_non_ascii_characters_does_not_crash(client, monkeypatch):
    import app

    monkeypatch.setattr(app, "PASSWORD", "sécret")
    good = base64.b64encode("u:sécret".encode()).decode()
    assert client.get("/api/status", headers={"Authorization": "Basic " + good}).status_code == 200
    bad = base64.b64encode(b"u:wrong").decode()
    assert client.get("/api/status", headers={"Authorization": "Basic " + bad}).status_code == 401
    assert client.get("/api/status").status_code == 401
