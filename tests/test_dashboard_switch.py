import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dragonfly.dashboard.app import app


class FakeSwitch:
    name = "Bedroom light"
    host = "192.168.68.53"

    def __init__(self):
        self.on = True

    async def get_state(self):
        return self.on

    async def toggle(self):
        self.on = not self.on
        return self.on


def test_switch_status_toggle_and_live_dashboard(monkeypatch):
    switch = FakeSwitch()
    monkeypatch.setitem(app.state._state, "kasa_switch_controller", switch)
    monkeypatch.setitem(app.state._state, "kasa_switch_config", None)

    with TestClient(app) as client:
        expected = {"name": "Bedroom light", "timezone": "America/Los_Angeles"}
        assert client.get("/api/switch").json() == {**expected, "on": True}
        assert client.post("/api/switch/toggle").json() == {**expected, "on": False}
        assert client.get("/api/switch").json() == {**expected, "on": False}
        page = client.get("/").text
        assert "<h2>Service health</h2>" in page
        assert "<h2>Devices</h2>" in page
        assert "id=\"switch-toggle\"" in page
        assert "Daily auto-off at 12:00 AM and 12:00 PM" in page
        assert 'id="switch-card" class="card switch-card"' in page
        assert 'id="physical-grid" class="physical-grid"' in page
        assert ".physical-grid { display: contents; }" in page
        assert ".device-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in page
        assert "setInterval(refreshSwitch, 1000)" in page
        assert "repeat(auto-fit, minmax(min(100%, 390px), 1fr))" in page


def test_switch_api_reports_unavailable_controller(monkeypatch):
    class UnavailableSwitch(FakeSwitch):
        async def get_state(self):
            raise RuntimeError("network unavailable")

    monkeypatch.setitem(app.state._state, "kasa_switch_controller", UnavailableSwitch())
    monkeypatch.setitem(app.state._state, "kasa_switch_config", None)

    with TestClient(app) as client:
        response = client.get("/api/switch")

    assert response.status_code == 503
    assert response.json()["detail"] == "Switch unavailable"


def test_module_api_classifies_services_and_devices(monkeypatch):
    class FakeRegistry:
        def all(self):
            return [
                SimpleNamespace(
                    module_id=module_id,
                    module_type=module_type,
                    online=True,
                    last_seen=time.time(),
                    recording_active=False,
                    recording_last_event=None,
                    recording_last_segment=None,
                    details={},
                )
                for module_id, module_type in (
                    ("_capture", "capture_process"),
                    ("_storage", "storage"),
                    ("OW", "camera"),
                )
            ]

    monkeypatch.setitem(app.state._state, "registry", FakeRegistry())
    monkeypatch.setitem(app.state._state, "camera_config", {})
    monkeypatch.setitem(app.state._state, "kasa_switch_controller", FakeSwitch())
    monkeypatch.setitem(app.state._state, "kasa_switch_config", None)

    with TestClient(app) as client:
        modules = client.get("/api/modules").json()

    by_id = {module["module_id"]: module for module in modules}
    assert by_id["_capture"]["module_type"] == "capture_process"
    assert by_id["_storage"]["module_type"] == "storage"
    assert by_id["_kasa_switch"]["module_type"] == "service"
    assert by_id["OW"]["module_type"] == "camera"