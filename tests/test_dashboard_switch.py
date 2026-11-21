import time
from types import SimpleNamespace
from fastapi import HTTPException

from fastapi.testclient import TestClient

from dragonfly.services.dfly_dashboard import app as dashboard_module

app = dashboard_module.app


class FakeSwitchController:
    def __init__(self):
        self.name = "Bedroom light"
        self.host = "192.168.68.53"

    async def get_state(self):
        return True

    async def toggle(self):
        return False

def _configure_switch(monkeypatch, controller):
    monkeypatch.setitem(
        app.state._state,
        "kasa_switch_config",
        SimpleNamespace(
            name=controller.name,
            host=controller.host,
            timezone="America/Los_Angeles",
            gpio_pin=17,
        ),
    )

    async def device_request(_request, method, path):
        if path == "/switch/toggle":
            return {"name": controller.name, "host": controller.host, "on": False, "timezone": "America/Los_Angeles"}
        return {"name": controller.name, "host": controller.host, "on": True, "timezone": "America/Los_Angeles"}

    monkeypatch.setattr(dashboard_module, "_device_request", device_request)


def test_service_health_uses_compact_table_with_alert_column():
    with TestClient(app) as client:
        page = client.get("/").text

    assert 'id="service-rows"' in page
    assert '<th scope="col">Alert / error</th>' in page
    assert "function renderServiceRow(m)" in page
    assert "details.service || (m.module_id === '_storage' ? 'storage' : m.module_id)" in page
    assert "m.module_id !== '_kasa_switch'" in page
    assert "if (details.error) alerts.push" in page
    assert "alerts.push('Unavailable')" in page
    assert "fmtBytes(details.used_bytes)" in page
    assert "fmtBytes(details.total_bytes)" in page
    assert "details.used_pct" in page


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
    _configure_switch(monkeypatch, switch)
    switch.on = True

    async def device_request(_request, _method, path):
        if path == "/switch/toggle":
            switch.on = not switch.on
        return {
            "name": switch.name,
            "host": switch.host,
            "timezone": "America/Los_Angeles",
            "on": switch.on,
        }

    monkeypatch.setattr(dashboard_module, "_device_request", device_request)

    with TestClient(app) as client:
        expected = {
            "name": "Bedroom light",
            "host": "192.168.68.53",
            "timezone": "America/Los_Angeles",
        }
        assert client.get("/api/switch").json() == {**expected, "on": True}
        assert client.post("/api/switch/toggle").json() == {**expected, "on": False}
        assert client.get("/api/switch").json() == {**expected, "on": False}
        page = client.get("/").text
        assert "<h2>Services &amp; checks</h2>" in page
        assert "<h2>Devices</h2>" in page
        assert page.index("<h2>Services &amp; checks</h2>") < page.index("<h2>Devices</h2>")
        assert "id=\"switch-toggle\"" in page
        assert "Daily auto-off at 12:00 AM and 12:00 PM" in page
        assert 'id="switch-card" class="card switch-card"' in page
        assert 'id="physical-grid" class="physical-grid"' in page
        assert ".physical-grid { display: contents; }" in page
        assert ".device-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in page
        assert "setInterval(refreshSwitch, 1000)" in page
        assert "repeat(auto-fit, minmax(min(100%, 390px), 1fr))" in page


def test_switch_api_reports_unavailable_device_service(monkeypatch):
    _configure_switch(monkeypatch, FakeSwitchController())

    async def unavailable(*_args):
        raise HTTPException(503, "Device service unavailable")

    monkeypatch.setattr(dashboard_module, "_device_request", unavailable)

    with TestClient(app) as client:
        response = client.get("/api/switch")

    assert response.status_code == 503
    assert response.json()["detail"] == "Device service unavailable"


def test_devices_page_only_contains_device_controls():
    with TestClient(app) as client:
        response = client.get("/devices")
        dashboard = client.get("/").text

    assert response.status_code == 200
    assert "Connected devices" in response.text
    assert 'id="switch-toggle"' in response.text
    assert "fetch('/api/modules'" in response.text
    assert "Services &amp; checks" not in response.text
    assert "historyChart" not in response.text
    assert 'href="/devices"' in dashboard


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
    _configure_switch(monkeypatch, FakeSwitchController())

    async def switch_status(*_args):
        return {"on": True, "host": "192.168.68.53"}

    monkeypatch.setattr(dashboard_module, "_device_request", switch_status)

    with TestClient(app) as client:
        modules = client.get("/api/modules").json()

    by_id = {module["module_id"]: module for module in modules}
    assert by_id["_capture"]["module_type"] == "capture_process"
    assert by_id["_storage"]["module_type"] == "storage"
    assert by_id["_kasa_switch"]["module_type"] == "service"
    assert by_id["OW"]["module_type"] == "camera"