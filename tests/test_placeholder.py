from dragonfly.hub.registry import DeviceRegistry


def test_registry_touch_and_get():
    registry = DeviceRegistry()
    registry.touch("OW", "camera")
    status = registry.get("OW")
    assert status is not None
    assert status.module_type == "camera"
    assert status.online is True


def test_registry_touch_offline():
    registry = DeviceRegistry()
    registry.touch("OW", "camera", online=False)
    status = registry.get("OW")
    assert status.online is False


def test_registry_unknown_module():
    registry = DeviceRegistry()
    assert registry.get("nope") is None
