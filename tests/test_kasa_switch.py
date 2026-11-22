import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dragonfly.services.dfly_devices.kasa_switch import next_auto_off
from dragonfly.services.dfly_devices.kasa_switch import KasaSwitchController


def test_next_auto_off_uses_noon_and_midnight_pacific():
    zone = ZoneInfo("America/Los_Angeles")

    assert next_auto_off(datetime(2026, 11, 11, 11, 30, tzinfo=zone), zone.key) == datetime(
        2026, 11, 11, 12, 0, tzinfo=zone
    )
    assert next_auto_off(datetime(2026, 11, 11, 12, 0, tzinfo=zone), zone.key) == datetime(
        2026, 11, 12, 0, 0, tzinfo=zone
    )
    assert next_auto_off(datetime(2026, 11, 11, 23, 30, tzinfo=zone), zone.key) == datetime(
        2026, 11, 12, 0, 0, tzinfo=zone
    )


def test_next_auto_off_tracks_daylight_saving_time():
    zone = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 3, 8, 1, 30, tzinfo=zone)

    target = next_auto_off(now, zone.key)

    assert target == datetime(2026, 3, 8, 12, 0, tzinfo=zone)
    assert target.utcoffset().total_seconds() == -7 * 60 * 60


def test_next_auto_off_requires_timezone_aware_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        next_auto_off(datetime(2026, 11, 11, 11, 30), "America/Los_Angeles")


def test_kasa_rescan_updates_host_for_matching_device_id(monkeypatch):
    class FakeDevice:
        def __init__(self, device_id):
            self.device_id = device_id
            self.disconnected = False

        async def disconnect(self):
            self.disconnected = True

    other = FakeDevice("AA:BB:CC:DD:EE:FF")
    switch = FakeDevice("20:E1:5D:1C:CB:B5")

    async def discover(**kwargs):
        assert kwargs["discovery_timeout"] == 5
        return {"192.168.68.49": other, "192.168.68.50": switch}

    monkeypatch.setattr("dragonfly.services.dfly_devices.kasa_switch.Discover.discover", discover)
    controller = KasaSwitchController(
        "192.168.68.53", "Bedroom light", "20:e1:5d:1c:cb:b5"
    )

    assert asyncio.run(controller.rescan()) == "192.168.68.50"
    assert controller.host == "192.168.68.50"
    assert other.disconnected and switch.disconnected


def test_kasa_rescan_does_not_change_host_when_device_is_missing(monkeypatch):
    device = type("FakeDevice", (), {"disconnect": lambda self: asyncio.sleep(0)})()

    async def discover(**kwargs):
        return {"192.168.68.49": device}

    monkeypatch.setattr("dragonfly.services.dfly_devices.kasa_switch.Discover.discover", discover)
    controller = KasaSwitchController(
        "192.168.68.53", "Bedroom light", "20:e1:5d:1c:cb:b5"
    )

    with pytest.raises(LookupError, match="not uniquely discovered"):
        asyncio.run(controller.rescan())
    assert controller.host == "192.168.68.53"