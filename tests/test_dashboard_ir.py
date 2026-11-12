from types import SimpleNamespace

from fastapi.testclient import TestClient

from dragonfly.dashboard.app import app
from dragonfly.hub.config import CameraConfig


def test_camera_ir_status_and_cycle_are_scoped_to_live_config(monkeypatch):
    class FakeController:
        async def get_state(self):
            return {"mode": "Off", "available_modes": ["Auto", "Off"], "next_mode": "Auto"}

        async def cycle_mode(self):
            return {"mode": "Auto", "available_modes": ["Auto", "Off"], "next_mode": "Off"}

        async def close(self):
            pass

    config = SimpleNamespace(cameras=[
        CameraConfig(
            id="OW",
            name="Outdoor West",
            rtsp_url="rtsp://admin:secret@camera.local/main",
            ir_control=True,
        )
    ])
    monkeypatch.setitem(app.state._state, "camera_config_loader", lambda: config)
    monkeypatch.setitem(app.state._state, "camera_ir_controllers", {"OW": FakeController()})

    with TestClient(app) as client:
        dashboard = client.get("/").text
        devices = client.get("/devices").text
        for page in (dashboard, devices):
            assert 'type="checkbox" data-ir-camera-id=' in page
            assert ".camera-ir-card { display: grid; grid-template-columns: minmax(0, 1fr) auto;" in page
        assert client.get("/api/cameras/OW/ir").json() == {
            "mode": "Off",
            "available_modes": ["Auto", "Off"],
            "next_mode": "Auto",
        }
        assert client.post("/api/cameras/OW/ir/cycle").json() == {
            "mode": "Auto",
            "available_modes": ["Auto", "Off"],
            "next_mode": "Off",
        }
        assert client.get("/api/cameras/unknown/ir").status_code == 404
        assert client.post("/api/cameras/unknown/ir/cycle").status_code == 404


def test_ir_opt_in_is_reloaded_without_restarting_portal(monkeypatch):
    class FakeController:
        async def get_state(self):
            return {"mode": "Off", "available_modes": ["Auto", "Off"], "next_mode": "Auto"}

        async def close(self):
            pass

    config = [SimpleNamespace(cameras=[
        CameraConfig(id="OW", name="Outdoor West", rtsp_url="rtsp://admin:secret@camera.local/main")
    ])]
    monkeypatch.setitem(app.state._state, "camera_config_loader", lambda: config[0])
    monkeypatch.setitem(app.state._state, "camera_ir_controllers", {})
    monkeypatch.setattr("dragonfly.dashboard.app.ReolinkIrController", lambda _url: FakeController())

    with TestClient(app) as client:
        assert client.get("/api/cameras/OW/ir").status_code == 404
        config[0] = SimpleNamespace(cameras=[
            CameraConfig(
                id="OW",
                name="Outdoor West",
                rtsp_url="rtsp://admin:secret@camera.local/main",
                ir_control=True,
            )
        ])
        response = client.get("/api/cameras/OW/ir")
        assert response.status_code == 200
        assert response.json()["mode"] == "Off"