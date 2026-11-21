from __future__ import annotations

from datetime import datetime
from pathlib import Path


def compute_segment_path(dt: datetime, root: Path, segment_seconds: int) -> Path:
    """Return the file path for a segment's nominal start time."""
    per_hour = 3600 // segment_seconds
    index = ((dt.minute * 60 + dt.second) // segment_seconds) % per_hour + 1
    return root / dt.strftime("%Y-%m-%d") / dt.strftime("%H") / f"{index}.mp4"