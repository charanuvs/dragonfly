"""Segmented video recorder for an RTSP camera.

Captures the camera's stream via `ffmpeg`, re-encoded to a fixed fps/bitrate
(independent of whatever the camera natively outputs), and writes it as
overlapping fixed-length segments so there is no gap in footage at file
boundaries. See docs/recording.md for the storage math behind the defaults.

Layout on disk:
    <recordings_root>/<module_id>/YYYY-MM-DD/HH/<N>.mp4
        N is 1..(3600/segment_seconds) within each hour, restarting every hour.
        HH and the date are the segment's *nominal* (scheduled) start time.

Cutover strategy: a new ffmpeg process is launched every `segment_seconds`,
and each process runs for `segment_seconds + overlap_seconds` before exiting
on its own. So when segment N+1 starts, segment N is still recording its
last `overlap_seconds` — both files contain that window, meaning a hiccup
right at the cutover can never produce an actual gap in footage.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from dragonfly.hub.eventbus import EventBus
from dragonfly.modules.base import SensorModule

log = logging.getLogger("dragonfly.modules.camera.recorder")


def compute_segment_path(dt: datetime, root: Path, segment_seconds: int) -> Path:
    """Where segment starting at (nominal) time `dt` should be written.

    File numbers restart every hour: 1..(3600/segment_seconds), assigned by
    the segment's scheduled start time regardless of small early/late drift.
    """
    per_hour = 3600 // segment_seconds
    index = ((dt.minute * 60 + dt.second) // segment_seconds) % per_hour + 1
    return root / dt.strftime("%Y-%m-%d") / dt.strftime("%H") / f"{index}.mp4"


class SegmentedRecorder(SensorModule):
    module_type = "camera_recorder"

    def __init__(
        self,
        module_id: str,
        bus: EventBus,
        rtsp_url: str,
        recordings_root: str,
        fps: int = 10,
        bitrate_kbps: int = 200,
        segment_seconds: int = 300,
        overlap_seconds: int = 10,
        retention_days: int = 3,
    ) -> None:
        super().__init__(module_id, bus)
        self.rtsp_url = rtsp_url
        self.root = Path(recordings_root) / module_id
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.segment_seconds = segment_seconds
        self.overlap_seconds = overlap_seconds
        self.retention_days = retention_days

        self._stop = threading.Event()
        self._record_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._processes: list[tuple[subprocess.Popen, object]] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found on PATH — install it (apt install ffmpeg)")
        self.root.mkdir(parents=True, exist_ok=True)
        self._record_thread = threading.Thread(
            target=self._record_loop, daemon=True, name=f"recorder-{self.module_id}"
        )
        self._record_thread.start()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name=f"cleanup-{self.module_id}"
        )
        self._cleanup_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for proc, log_f in self._processes:
                if proc.poll() is None:
                    proc.terminate()
                log_f.close()
            self._processes = []
        if self._record_thread:
            self._record_thread.join(timeout=5)
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)

    # -- recording --

    def _record_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            # Epoch-aligned boundaries line up with wall-clock 5-minute marks
            # in any real-world timezone (all UTC offsets are multiples of 5 min).
            next_boundary = (int(now // self.segment_seconds) + 1) * self.segment_seconds
            if self._stop.wait(next_boundary - now):
                break
            self._launch_segment(next_boundary)
            self._reap_finished()

    def _launch_segment(self, boundary_epoch: float) -> None:
        dt = datetime.fromtimestamp(boundary_epoch).astimezone()
        path = compute_segment_path(dt, self.root, self.segment_seconds)
        path.parent.mkdir(parents=True, exist_ok=True)
        duration = self.segment_seconds + self.overlap_seconds
        cmd = [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "warning",
            "-rtsp_transport", "tcp", "-rw_timeout", "15000000",
            "-i", self.rtsp_url,
            "-t", str(duration),
            "-vf", f"fps={self.fps}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{self.bitrate_kbps}k",
            "-maxrate", f"{self.bitrate_kbps}k",
            "-bufsize", f"{self.bitrate_kbps * 2}k",
            "-an",
            "-movflags", "+faststart",
            str(path),
        ]
        log.info("recording segment -> %s", path)
        log_f = open(path.with_suffix(".log"), "w")
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_f)
        with self._lock:
            self._processes.append((proc, log_f))
        self.publish("recording", json.dumps({"segment": str(path), "started": boundary_epoch}))

    def _reap_finished(self) -> None:
        with self._lock:
            still_running = []
            for proc, log_f in self._processes:
                if proc.poll() is None:
                    still_running.append((proc, log_f))
                    continue
                log_f.close()
                if proc.returncode != 0:
                    log.warning(
                        "ffmpeg segment exited %s — see %s.log", proc.returncode, "<segment>"
                    )
            self._processes = still_running

    # -- retention cleanup --

    def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_cleanup()
            except Exception:
                log.exception("cleanup pass failed")
            if self._stop.wait(3600):  # check hourly
                break

    def _run_cleanup(self) -> None:
        if not self.root.exists():
            return

        cutoff = datetime.now().date() - timedelta(days=self.retention_days)
        for day_dir in sorted(self.root.iterdir()):
            if not day_dir.is_dir():
                continue
            try:
                day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                log.info("removing expired recordings (older than %dd): %s", self.retention_days, day_dir)
                shutil.rmtree(day_dir, ignore_errors=True)

        # Safety net: date-based retention assumes the configured bitrate holds
        # roughly steady. If actual usage still creeps too high, drop the
        # oldest remaining day(s) rather than risk filling the disk.
        self._emergency_cleanup_if_full()

    def _emergency_cleanup_if_full(self, threshold_pct: float = 90.0) -> None:
        usage = shutil.disk_usage(self.root)
        while usage.used / usage.total * 100 > threshold_pct:
            day_dirs = sorted(d for d in self.root.iterdir() if d.is_dir())
            if not day_dirs:
                break
            oldest = day_dirs[0]
            log.warning("disk >%.0f%% full — emergency-removing %s", threshold_pct, oldest)
            shutil.rmtree(oldest, ignore_errors=True)
            usage = shutil.disk_usage(self.root)
