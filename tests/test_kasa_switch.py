from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dragonfly.hub.kasa_switch import next_auto_off


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