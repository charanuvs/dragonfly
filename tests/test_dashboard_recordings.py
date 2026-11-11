from fastapi.testclient import TestClient
import pytest

from dragonfly.dashboard.app import app


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    root = tmp_path / "recordings"
    root.mkdir()
    monkeypatch.setitem(app.state._state, "recordings_path", root)
    with TestClient(app) as client:
        yield root, client


def test_browse_and_play_recording(recordings):
    root, client = recordings
    folder = root / "front door" / "2026-11-10"
    folder.mkdir(parents=True)
    (folder / "1.mp4").write_bytes(b"0123456789")
    (folder / "notes.txt").write_text("private")

    assert 'href="/recordings?path=${encodeURIComponent(m.module_id)}"' in client.get("/").text
    assert client.get("/recordings").status_code == 200
    assert client.get("/api/recordings").json()["entries"] == [
        {"name": "front door", "type": "directory"}
    ]
    assert client.get("/api/recordings", params={"path": "front door/2026-11-10"}).json()[
        "entries"
    ] == [{"name": "1.mp4", "type": "video"}]

    response = client.get(
        "/api/recordings/video",
        params={"path": "front door/2026-11-10/1.mp4"},
        headers={"Range": "bytes=2-4"},
    )
    assert response.status_code == 206
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == b"234"


def test_cannot_browse_or_play_outside_recordings(recordings, tmp_path):
    root, client = recordings
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "escape.mp4").symlink_to(outside / "secret.mp4")
    (root / "notes.txt").write_text("private")

    assert client.get("/api/recordings").json()["entries"] == []
    for path in ("../outside", "escape"):
        assert client.get("/api/recordings", params={"path": path}).status_code == 404
    for path in ("../outside/secret.mp4", "escape/secret.mp4", "escape.mp4", "notes.txt"):
        assert client.get("/api/recordings/video", params={"path": path}).status_code == 404


def test_missing_recordings(recordings, monkeypatch):
    root, client = recordings
    assert client.get("/api/recordings", params={"path": "missing"}).status_code == 404
    assert client.get("/api/recordings/video", params={"path": "missing.mp4"}).status_code == 404
    monkeypatch.setitem(app.state._state, "recordings_path", None)
    assert client.get("/api/recordings").status_code == 404