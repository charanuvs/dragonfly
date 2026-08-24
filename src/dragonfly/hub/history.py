"""In-memory rolling history of system stats, for the dashboard's trend chart.

Keeps a bounded number of samples (default: 24h at 1-minute resolution, 1440
samples) in memory — no database needed, and it's a tiny footprint even on a
resource-constrained Pi (a handful of floats per sample).
"""
from __future__ import annotations

import threading
import time
from collections import deque

from dragonfly.hub.sysinfo import get_system_stats


class SystemStatsHistory:
    def __init__(self, disk_path: str, interval_s: int = 60, max_samples: int = 1440) -> None:
        self.disk_path = disk_path
        self.interval_s = interval_s
        self._samples: deque[dict] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="sysstats-history")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._samples)

    def _run(self) -> None:
        # Take one sample immediately so the chart isn't empty for the first
        # interval_s after startup.
        self._sample_once()
        while not self._stop.wait(self.interval_s):
            self._sample_once()

    def _sample_once(self) -> None:
        stats = get_system_stats(self.disk_path)
        sample = {
            "ts": time.time(),
            "cpu_percent": stats["cpu_percent"],
            "mem_percent": stats["mem_percent"],
            "disk_percent": stats.get("disk_percent"),
        }
        with self._lock:
            self._samples.append(sample)
