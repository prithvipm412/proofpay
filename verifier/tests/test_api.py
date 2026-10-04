"""Endpoints V-08..V-10 over HTTP, with the real child-process decoder (V-L5).

Covers the HTTP parts of V-T12 (400/413), V-S9 (410), V-S7 (507), V-05 (CORS) and V-04.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.images import MAX_UPLOAD_BYTES
from app.main import create_app, prepare
from tests.conftest import FIXTURES, FakeChain, fixture_hash, make_settings


def client_for(settings) -> TestClient:
    services = prepare(settings, FakeChain())
    return TestClient(create_app(services, start_threads=False))


@pytest.fixture
def client(tmp_path):
    with client_for(make_settings(tmp_path)) as c:
        yield c


def upload(client, data: bytes, name="photo.jpg", field="file"):
    return client.post("/upload", files={field: (name, data, "image/jpeg")})


def png(size, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size).save(buf, format="PNG")
    return buf.getvalue()


def test_V09_upload_returns_sha256_of_original_bytes(client):
    data = (FIXTURES / "a_after.jpg").read_bytes()
    r = upload(client, data)
    assert r.status_code == 200
    assert r.json() == {"sha256": fixture_hash("a_after.jpg")}
    assert upload(client, data).json() == r.json()  # existing hash returned


def test_VT12_invalid_bytes_named_jpg_400(client):
    r = upload(client, b"\xff\xd8\xff" + b"garbage" * 500)
    assert r.status_code == 400


def test_VT12_png_named_jpg_200_stored_as_png(client, tmp_path):
    r = upload(client, png((30, 20)), name="photo.jpg")
    assert r.status_code == 200
    sha = r.json()["sha256"]
    stored = list((tmp_path / "data").rglob(f"{sha[2:]}.*"))
    assert [p.suffix for p in stored if p.parent.name == "originals"] == [".png"]


@pytest.mark.parametrize("name", ["animated.png", "animated.webp"])
def test_VT12_animated_400(client, name):
    assert upload(client, (FIXTURES / name).read_bytes(), name=name).status_code == 400


def test_VT12_decompression_bomb_400(client):
    r = upload(client, png((5001, 5001), "L"), name="bomb.png")
    assert r.status_code == 400
    assert "pixels" in r.json()["error"]


def test_VT12_ten_mb_plus_one_byte_413(client, tmp_path):
    r = upload(client, b"\0" * (MAX_UPLOAD_BYTES + 1))
    assert r.status_code == 413
    assert list((tmp_path / "data").rglob("*.upload")) == []  # partial file removed


def test_VT12_exactly_ten_mb_is_not_413(client):
    assert upload(client, b"\0" * MAX_UPLOAD_BYTES).status_code == 400  # size OK, not an image


def test_VL1_large_content_length_refused_early(client):
    r = client.post(
        "/upload",
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=abc", "content-length": str(30 * 1024 * 1024)},
    )
    assert r.status_code == 413


def test_upload_needs_multipart_file_field(client):
    assert client.post("/upload", content=b"abc", headers={"content-type": "image/jpeg"}).status_code == 400
    assert upload(client, (FIXTURES / "a_after.jpg").read_bytes(), field="photo").status_code == 400


def test_VS9_uploads_after_admission_until_410(tmp_path):
    with client_for(make_settings(tmp_path, ADMISSION_UNTIL="2020-01-01T00:00:00Z")) as c:
        assert upload(c, (FIXTURES / "a_after.jpg").read_bytes()).status_code == 410


def test_VS7_storage_full_507(tmp_path):
    with client_for(make_settings(tmp_path, MAX_STORAGE_MB="300")) as c:  # 300 < 10 + 300 reserve
        assert upload(c, (FIXTURES / "a_after.jpg").read_bytes()).status_code == 507


def test_V10_files_serves_preview_only(client):
    sha = upload(client, (FIXTURES / "a_after.jpg").read_bytes()).json()["sha256"]
    r = client.get(f"/files/{sha}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(r.content)) as im:
        assert not im.getexif()  # preview, not the original with EXIF
    assert r.content != (FIXTURES / "a_after.jpg").read_bytes()
    assert client.get(f"/files/{sha[2:]}").status_code == 200  # without 0x


@pytest.mark.parametrize("bad", ["0x" + "ab" * 32, "nothex", "..%2F..%2Fproofpay.db", "0x1234"])
def test_V10_unknown_or_bad_hash_404(client, bad):
    assert client.get(f"/files/{bad}").status_code == 404


def test_V08_health_shape_and_503_without_worker_loop(client):
    r = client.get("/health")
    assert r.status_code == 503  # no loops run in this test (V-R9)
    body = r.json()
    for key in ("ready", "mode", "deploymentId", "model", "historyStatus", "eventLagBlocks",
                "oldestEligibleJobAgeSec", "unsettledJobs", "heartbeatAgeSec", "warnings", "failed"):
        assert key in body
    assert "test-key" not in r.text


def test_V05_cors_allows_only_configured_origins(client):
    ok = client.options(
        "/upload", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options(
        "/upload", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in bad.headers
