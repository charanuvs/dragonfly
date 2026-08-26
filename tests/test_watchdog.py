"""Tests for the watchdog's health checks and its monitoring pass."""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from dragonfly.hub.config import CameraConfig, HubConfig, StorageConfig, WatchdogConfig
from dragonfly.hub.registry import DeviceRegistry
from dragonfly.watchdog.checks import (
    check_camera,
    check_recording,
    check_service,
    check_storage,
)
from dragonfly.watchdog.monitor import Watchdog


# -- check_storage --

def test_check_storage_reports_not_mounted(tmp_path):
    with patch("dragonfly.watchdog.checks.os.path.ismount", return_value=False):
        result = check_storage(str(tmp_path), str(tmp_path))
    assert result["online"] is False
    assert result["healthy"] is False
    assert result["error"] == "not_mounted"


def test_check_storage_reports_unreadable_zombie_mount(tmp_path):
    # Mounted per ismount() but statvfs fails — exactly the state a
    # physically-yanked USB drive leaves behind.
    with patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.os.statvfs", side_effect=OSError("Input/output error")):
        result = check_storage(str(tmp_path), str(tmp_path))
    assert result["online"] is False
    assert result["healthy"] is False
    assert "unreadable" in result["error"]


def test_check_storage_healthy_reports_usage(tmp_path):
    with patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True):
        result = check_storage(str(tmp_path), str(tmp_path))
    assert result["online"] is True
    assert result["healthy"] is True
    assert result["full"] is False
    assert 0 <= result["used_pct"] <= 100
    assert result["total_bytes"] > 0


def test_check_storage_flags_full(tmp_path):
    fake = os.statvfs("/")
    with patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.os.statvfs") as statvfs:
        # 99% used: 100 blocks total, 1 available
        statvfs.return_value = type(
            "S", (), {"f_blocks": 100, "f_frsize": fake.f_frsize, "f_bavail": 1}
        )()
        result = check_storage(str(tmp_path), str(tmp_path), full_pct=95.0)
    assert result["full"] is True
    assert result["online"] is False
    assert result["healthy"] is True  # readable, just full


# -- check_recording --

def test_check_recording_no_files(tmp_path):
    result = check_recording(str(tmp_path), "OW")
    assert result["active"] is False
    assert result["error"] == "no_recordings"


def test_check_recording_active_for_fresh_file(tmp_path):
    seg = tmp_path / "OW" / "2026-08-25" / "23" / "1.mp4"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"x" * 1024)
    result = check_recording(str(tmp_path), "OW", segment_seconds=300)
    assert result["active"] is True
    assert result["newest_size_bytes"] == 1024
    assert result["newest_age_s"] < 5


def test_check_recording_inactive_for_stale_file(tmp_path):
    seg = tmp_path / "OW" / "2026-08-25" / "23" / "1.mp4"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"x")
    stale = time.time() - (300 + 60 + 120)
    os.utime(seg, (stale, stale))
    result = check_recording(str(tmp_path), "OW", segment_seconds=300, grace_s=60)
    assert result["active"] is False
    assert result["newest_age_s"] > 300


def test_check_recording_picks_newest_across_hours(tmp_path):
    old = tmp_path / "OW" / "2026-08-25" / "22" / "1.mp4"
    new = tmp_path / "OW" / "2026-08-25" / "23" / "1.mp4"
    for p in (old, new):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    past = time.time() - 4000
    os.utime(old, (past, past))
    result = check_recording(str(tmp_path), "OW")
    assert result["newest_segment"] == str(new)


# -- check_camera / check_service --

def test_check_camera_offline_when_unreachable():
    with patch("dragonfly.watchdog.checks.socket.create_connection", side_effect=OSError("refused")):
        result = check_camera("rtsp://192.0.2.1:554/stream")
    assert result["online"] is False
    assert result["host"] == "192.0.2.1"
    assert result["port"] == 554


def test_check_camera_online_when_reachable():
    with patch("dragonfly.watchdog.checks.socket.create_connection"):
        result = check_camera("rtsp://user:pw@10.0.0.5:554/stream")
    assert result["online"] is True
    assert result["host"] == "10.0.0.5"


def test_check_service_reports_active():
    with patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active\n", stderr="")
        result = check_service("dragonfly-capture")
    assert result["online"] is True
    assert result["state"] == "active"


def test_check_service_reports_inactive():
    with patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=3, stdout="inactive\n", stderr="")
        result = check_service("dragonfly-capture")
    assert result["online"] is False
    assert result["state"] == "inactive"


# -- Watchdog pass --

def _config(tmp_path, record=True) -> HubConfig:
    return HubConfig(
        storage=StorageConfig(
            recordings_path=str(tmp_path), mount_point=str(tmp_path)
        ),
        watchdog=WatchdogConfig(),
        cameras=[
            CameraConfig(
                id="OW", name="Outdoor West",
                rtsp_url="rtsp://10.0.0.5:554/s", record=record,
            )
        ],
    )


def test_watchdog_pass_populates_registry(tmp_path):
    registry = DeviceRegistry()
    seg = tmp_path / "OW" / "2026-08-25" / "23" / "1.mp4"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"x")

    wd = Watchdog(_config(tmp_path), registry, bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        wd.run_once()

    assert registry.get("_capture").online is True
    assert registry.get("_storage").online is True
    assert registry.get("OW").online is True
    assert registry.get("OW").recording_active is True
    # storage card carries usable detail rather than an opaque type string
    assert "used_pct" in registry.get("_storage").details


def test_watchdog_reports_current_file_on_the_camera(tmp_path):
    # The card shows which file is being written, so the camera's details
    # have to carry it — not just the separate recording_verified topic.
    registry = DeviceRegistry()
    seg = tmp_path / "OW" / "2026-08-25" / "23" / "7.mp4"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"x" * 2048)

    wd = Watchdog(_config(tmp_path), registry, bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        wd.run_once()

    details = registry.get("OW").details
    assert details["newest_segment"].endswith("2026-08-25/23/7.mp4")
    assert details["newest_size_bytes"] == 2048
    assert "newest_age_s" in details
    assert registry.get("OW").recording_last_segment.endswith("7.mp4")


def test_watchdog_marks_recording_inactive_when_storage_unhealthy(tmp_path):
    registry = DeviceRegistry()
    wd = Watchdog(_config(tmp_path), registry, bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=False), \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        wd.run_once()

    assert registry.get("_storage").online is False
    assert registry.get("OW").recording_active is False


def test_watchdog_repairs_unhealthy_storage_and_rechecks(tmp_path):
    registry = DeviceRegistry()
    wd = Watchdog(_config(tmp_path), registry, bus=None)
    # Unmounted on the first check, mounted on the recheck after repair —
    # i.e. the repair worked and the card should show healthy, not broken.
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", side_effect=[False, True]), \
         patch("dragonfly.watchdog.monitor.repair_storage") as repair, \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        repair.return_value = {"ok": True, "output": "remounted"}
        wd.run_once()

    repair.assert_called_once()
    assert registry.get("_storage").online is True


def test_watchdog_repair_is_rate_limited(tmp_path):
    wd = Watchdog(_config(tmp_path), DeviceRegistry(), bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=False), \
         patch("dragonfly.watchdog.monitor.repair_storage") as repair, \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        repair.return_value = {"ok": False, "error": "no drive"}
        wd.run_once()
        wd.run_once()
        wd.run_once()

    assert repair.call_count == 1  # subsequent passes suppressed


def test_watchdog_does_not_repair_when_disabled(tmp_path):
    config = _config(tmp_path)
    config.watchdog.repair_storage = False
    wd = Watchdog(config, DeviceRegistry(), bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=False), \
         patch("dragonfly.watchdog.monitor.repair_storage") as repair, \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        wd.run_once()

    repair.assert_not_called()


def test_watchdog_does_not_repair_a_merely_full_disk(tmp_path):
    # Full is readable and mounted — repair can't help, and running umount
    # against a working drive mid-recording would be actively harmful.
    fake = os.statvfs("/")
    wd = Watchdog(_config(tmp_path), DeviceRegistry(), bus=None)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.os.statvfs") as statvfs, \
         patch("dragonfly.watchdog.monitor.repair_storage") as repair, \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        statvfs.return_value = type(
            "S", (), {"f_blocks": 100, "f_frsize": fake.f_frsize, "f_bavail": 1}
        )()
        wd.run_once()

    repair.assert_not_called()


def test_watchdog_publishes_retained(tmp_path):
    bus = MagicMock()
    wd = Watchdog(_config(tmp_path, record=False), DeviceRegistry(), bus=bus)
    with patch("dragonfly.watchdog.checks.socket.create_connection"), \
         patch("dragonfly.watchdog.checks.os.path.ismount", return_value=True), \
         patch("dragonfly.watchdog.checks.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="active", stderr="")
        wd.run_once()

    assert bus.publish.called
    assert all(c.kwargs.get("retain") is True for c in bus.publish.call_args_list)


def test_watchdog_survives_a_failing_check(tmp_path):
    registry = DeviceRegistry()
    wd = Watchdog(_config(tmp_path), registry, bus=None)
    with patch("dragonfly.watchdog.checks.subprocess.run", side_effect=RuntimeError("boom")):
        # run_once propagates; the loop is what swallows it. Verify the loop
        # keeps going rather than dying on the first bad pass.
        wd._stop.set()  # one iteration only
        wd._loop()  # must not raise
