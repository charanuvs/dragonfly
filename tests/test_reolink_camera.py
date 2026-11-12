import asyncio

from dragonfly.hub.reolink_camera import IR_MODES, ReolinkIrController


def test_ir_modes_cycle_between_camera_supported_values(monkeypatch):
    class FakeHost:
        def __init__(self, *args, **kwargs):
            self._ir_settings = {0: {"state": "Off"}}
            self.changed_to = None

        async def get_host_data(self):
            pass

        def supported(self, channel, feature):
            return feature == "ir_lights"

        async def get_state(self, command, ch):
            return None

        async def set_ir_lights(self, channel, enable):
            self.changed_to = "Auto" if enable else "Off"
            self._ir_settings[channel]["state"] = self.changed_to

        async def logout(self):
            pass

    monkeypatch.setattr("dragonfly.hub.reolink_camera.Host", FakeHost)
    controller = ReolinkIrController("rtsp://admin:secret@camera.local:554/main")

    async def exercise():
        initial = await controller.get_state()
        changed = await controller.cycle_mode()
        restored = await controller.cycle_mode()
        return initial, changed, restored, controller._host.changed_to

    initial, changed, restored, last_set = asyncio.run(exercise())

    assert IR_MODES == ("Auto", "Off")
    assert initial == {"mode": "Off", "available_modes": ["Auto", "Off"], "next_mode": "Auto"}
    assert changed["mode"] == "Auto"
    assert restored["mode"] == "Off"
    assert last_set == "Off"


def test_ir_controller_requires_credentials():
    try:
        ReolinkIrController("rtsp://camera.local:554/main")
    except ValueError as exc:
        assert "credentials" in str(exc)
    else:
        raise AssertionError("expected a missing-credentials error")


def test_ir_controller_rejects_unreported_modes(monkeypatch):
    class FakeHost:
        def __init__(self, *args, **kwargs):
            self._ir_settings = {0: {"state": "On"}}

        async def get_host_data(self):
            pass

        def supported(self, channel, feature):
            return True

        async def get_state(self, command, ch):
            pass

    monkeypatch.setattr("dragonfly.hub.reolink_camera.Host", FakeHost)
    controller = ReolinkIrController("rtsp://admin:secret@camera.local:554/main")

    try:
        asyncio.run(controller.get_state())
    except RuntimeError as exc:
        assert "unsupported IR mode" in str(exc)
    else:
        raise AssertionError("expected an unsupported mode error")