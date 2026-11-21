from types import SimpleNamespace

from fastapi.testclient import TestClient

from dragonfly.services.dfly_devices.app import app


def test_device_service_health_and_unconfigured_switch(monkeypatch):
    monkeypatch.setitem(app.state._state, "kasa_switch_config", None)
    monkeypatch.setitem(app.state._state, "kasa_switch_controller", None)

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/switch").status_code == 404
        assert client.post("/switch/toggle").status_code == 404


def test_device_service_ir_requires_camera_opt_in(monkeypatch):
    monkeypatch.setitem(
        app.state._state,
        "camera_config_loader",
        lambda: SimpleNamespace(cameras=[]),
    )

    with TestClient(app) as client:
        assert client.get("/cameras/OW/ir").status_code == 404
        assert client.post("/cameras/OW/ir/cycle").status_code == 404