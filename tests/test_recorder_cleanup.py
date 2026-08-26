"""Tests for SegmentedRecorder's storage-usage-based (watermark) cleanup,
plus the optional retention_days ceiling and empty-directory pruning.
"""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragonfly.modules.camera.recorder import SegmentedRecorder

DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


def _make_recorder(tmp_path: Path, **kwargs) -> SegmentedRecorder:
    return SegmentedRecorder(
        module_id="OW",
        bus=None,
        rtsp_url="rtsp://example/stream",
        recordings_root=str(tmp_path),
        **kwargs,
    )


def _touch_segment(recordings_root: Path, module_id: str, day: str, hour: str, n: int) -> Path:
    p = recordings_root / module_id / day / hour / f"{n}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    p.with_suffix(".log").write_text("")
    return p


def test_iter_segments_oldest_first_orders_by_layout_not_name(tmp_path):
    recorder = _make_recorder(tmp_path)
    expected = [
        _touch_segment(tmp_path, "OW", "2026-08-01", "23", 12),
        _touch_segment(tmp_path, "OW", "2026-08-02", "00", 1),
        _touch_segment(tmp_path, "OW", "2026-08-02", "00", 2),
        _touch_segment(tmp_path, "OW", "2026-08-02", "00", 10),  # numeric order, not "10" < "2"
    ]
    assert list(recorder._iter_segments_oldest_first()) == expected


def test_watermark_cleanup_deletes_oldest_segments_until_low_watermark(tmp_path):
    recorder = _make_recorder(tmp_path, high_watermark_pct=90, low_watermark_pct=50)
    for n in range(1, 11):
        _touch_segment(tmp_path, "OW", "2026-08-01", "00", n)

    # Fake disk usage: 1 "unit" per remaining segment file, out of a fixed
    # total of 10 units — so 10 files = 100%, 5 files = 50%, etc.
    def fake_disk_usage(_path):
        remaining = sum(1 for _ in recorder.root.rglob("*.mp4"))
        return DiskUsage(total=10, used=remaining, free=10 - remaining)

    with patch("dragonfly.modules.camera.recorder.shutil.disk_usage", side_effect=fake_disk_usage):
        recorder._watermark_cleanup()

    remaining = sorted(recorder.root.rglob("*.mp4"), key=lambda p: int(p.stem))
    assert [int(p.stem) for p in remaining] == [6, 7, 8, 9, 10]
    # log sidecars for deleted segments should be gone too
    assert not (recorder.root / "2026-08-01" / "00" / "1.log").exists()


def test_watermark_cleanup_never_deletes_an_active_segment(tmp_path):
    recorder = _make_recorder(tmp_path, high_watermark_pct=90, low_watermark_pct=10)
    oldest = _touch_segment(tmp_path, "OW", "2026-08-01", "00", 1)
    _touch_segment(tmp_path, "OW", "2026-08-01", "00", 2)

    active_proc = MagicMock()
    active_proc.poll.return_value = None
    recorder._processes.append({"proc": active_proc, "log_f": MagicMock(), "path": oldest})

    def fake_disk_usage(_path):
        remaining = sum(1 for _ in recorder.root.rglob("*.mp4"))
        return DiskUsage(total=10, used=remaining * 5, free=10 - remaining * 5)

    with patch("dragonfly.modules.camera.recorder.shutil.disk_usage", side_effect=fake_disk_usage):
        recorder._watermark_cleanup()

    assert oldest.exists()  # never deleted despite being the oldest


def test_retention_ceiling_cleanup_removes_days_older_than_cutoff(tmp_path):
    recorder = _make_recorder(tmp_path, retention_days=3)
    _touch_segment(tmp_path, "OW", "2000-01-01", "00", 1)
    old_day = recorder.root / "2000-01-01"

    recorder._retention_ceiling_cleanup()

    assert not old_day.exists()


def test_prune_empty_dirs_removes_emptied_hour_and_day_folders(tmp_path):
    recorder = _make_recorder(tmp_path)
    seg = _touch_segment(tmp_path, "OW", "2026-08-01", "00", 1)
    seg.unlink()
    seg.with_suffix(".log").unlink()

    recorder._prune_empty_dirs()

    assert not (recorder.root / "2026-08-01" / "00").exists()
    assert not (recorder.root / "2026-08-01").exists()
