from datetime import datetime
from pathlib import Path

from dragonfly.modules.camera.recorder import compute_segment_path


def test_first_segment_of_hour():
    dt = datetime(2026, 8, 2, 14, 0, 0)
    path = compute_segment_path(dt, Path("/rec"), segment_seconds=300)
    assert path == Path("/rec/2026-08-02/14/1.mp4")


def test_last_segment_of_hour():
    dt = datetime(2026, 8, 2, 14, 55, 0)
    path = compute_segment_path(dt, Path("/rec"), segment_seconds=300)
    assert path == Path("/rec/2026-08-02/14/12.mp4")


def test_hour_resets_numbering():
    dt = datetime(2026, 8, 2, 15, 0, 0)
    path = compute_segment_path(dt, Path("/rec"), segment_seconds=300)
    assert path == Path("/rec/2026-08-02/15/1.mp4")


def test_mid_hour_segment():
    dt = datetime(2026, 8, 2, 14, 20, 0)
    path = compute_segment_path(dt, Path("/rec"), segment_seconds=300)
    # 20 min in -> 4th five-minute slot
    assert path == Path("/rec/2026-08-02/14/5.mp4")
