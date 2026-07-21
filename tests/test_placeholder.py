from dragonfly.hub.registry import DeviceRegistry


def test_registry_touch_and_get():
    registry = DeviceRegistry()
    registry.touch("cam-front-door", "camera")
    status = registry.get("cam-front-door")
    assert status is not None
    assert status.module_type == "camera"
