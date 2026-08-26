"""Tests that SegmentedRecorder degrades gracefully when storage disappears
out from under it — never crashing the recording thread, always reporting
"not recording" over MQTT instead.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from dragonfly.modules.camera.recorder import SegmentedRecorder


def _make_recorder(tmp_path, bus, **kwargs) -> SegmentedRecorder:
    return SegmentedRecorder(
        module_id="OW",
        bus=bus,
        rtsp_url="rtsp://example/stream",
        recordings_root=str(tmp_path),
        **kwargs,
    )


def test_launch_segment_failure_does_not_raise_and_reports_not_recording(tmp_path):
    fake_bus = MagicMock()
    recorder = _make_recorder(tmp_path, fake_bus)

    # Force mkdir to fail: put a plain file where the module's root
    # directory needs to be, so creating subdirectories under it raises
    # OSError — simulating the drive being gone/unwritable.
    (tmp_path / "OW").write_text("not a directory")

    recorder._launch_segment(boundary_epoch=0, duration=60)  # must not raise

    assert fake_bus.publish.called
    _module_id, subtopic, payload = fake_bus.publish.call_args[0]
    assert subtopic == "recording"
    data = json.loads(payload)
    assert data["active"] is False
    assert data["error"] == "write_failed"
    assert recorder._processes == []  # nothing was tracked as running


def test_launch_segment_skips_write_when_mount_point_not_mounted(tmp_path):
    fake_bus = MagicMock()
    recorder = _make_recorder(tmp_path, fake_bus, mount_point="/mnt/dragonfly-hdd")

    with patch("dragonfly.modules.camera.recorder.os.path.ismount", return_value=False):
        recorder._launch_segment(boundary_epoch=0, duration=60)  # must not raise

    # Nothing should have been written to disk at all — not even the
    # directory structure — since the whole point is to never touch the
    # filesystem when the drive isn't actually mounted there.
    assert not (tmp_path / "OW").exists()
    assert fake_bus.publish.called
    _module_id, subtopic, payload = fake_bus.publish.call_args[0]
    assert subtopic == "recording"
    data = json.loads(payload)
    assert data["active"] is False
    assert data["error"] == "not_mounted"
    assert recorder._processes == []


def test_launch_segment_proceeds_when_mount_point_is_mounted(tmp_path):
    fake_bus = MagicMock()
    recorder = _make_recorder(tmp_path, fake_bus, mount_point="/mnt/dragonfly-hdd")

    with patch("dragonfly.modules.camera.recorder.os.path.ismount", return_value=True), \
         patch("dragonfly.modules.camera.recorder.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(poll=MagicMock(return_value=None))
        recorder._launch_segment(boundary_epoch=0, duration=60)

    assert (tmp_path / "OW").exists()  # directory structure was created
    assert len(recorder._processes) == 1
    _module_id, subtopic, payload = fake_bus.publish.call_args[0]
    data = json.loads(payload)
    assert data["active"] is True


def test_reap_finished_publishes_not_recording_on_nonzero_exit(tmp_path):
    fake_bus = MagicMock()
    recorder = _make_recorder(tmp_path, fake_bus)

    dead_proc = MagicMock()
    dead_proc.poll.return_value = 1
    dead_proc.returncode = 1
    segment_path = tmp_path / "OW" / "2026-08-25" / "14" / "1.mp4"
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    segment_path.touch()
    recorder._processes.append({"proc": dead_proc, "log_f": MagicMock(), "path": segment_path})

    recorder._reap_finished()

    assert recorder._processes == []
    assert fake_bus.publish.called
    _module_id, subtopic, payload = fake_bus.publish.call_args[0]
    assert subtopic == "recording"
    data = json.loads(payload)
    assert data["active"] is False
    assert data["error"] == "segment_failed"


def test_reap_finished_does_not_publish_on_clean_exit(tmp_path):
    fake_bus = MagicMock()
    recorder = _make_recorder(tmp_path, fake_bus)

    clean_proc = MagicMock()
    clean_proc.poll.return_value = 0
    clean_proc.returncode = 0
    segment_path = tmp_path / "OW" / "2026-08-25" / "14" / "1.mp4"
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    segment_path.touch()
    recorder._processes.append({"proc": clean_proc, "log_f": MagicMock(), "path": segment_path})

    recorder._reap_finished()

    assert recorder._processes == []
    assert not fake_bus.publish.called
