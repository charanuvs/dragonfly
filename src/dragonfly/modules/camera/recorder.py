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

Cleanup is storage-usage-based, not day-based: once disk usage crosses
`high_watermark_pct`, the single oldest completed segment file gets deleted,
then usage is rechecked — repeating one file at a time until usage drops
back to `low_watermark_pct`. Deleting a whole segment file is a cheap
metadata-only operation (no rewriting file contents), and since every
segment always starts on a fresh keyframe, there's never a corrupt partial
file left behind — this is the practical, container-format-friendly version
of "truncate old footage off the front as a ring buffer" (a real byte-level
ring buffer isn't feasible for MP4 or any GOP-based codec — see
docs/recording.md). `retention_days`, if set, is an independent hard
ceiling (e.g. a privacy/legal cutoff), not the primary mechanism.

Resilient to storage disappearing out from under it (e.g. the recordings
drive gets unplugged): a failed segment launch or write never crashes the
recording thread, it's caught, reported over MQTT as "not recording", and
retried on the next scheduled segment. This is deliberately just a recorder
concern — heartbeat and live view run independently in the same process and
are unaffected by a storage problem.
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
        high_watermark_pct: float = 90.0,
        low_watermark_pct: float = 80.0,
        retention_days: int | None = None,
    ) -> None:
        super().__init__(module_id, bus)
        self.rtsp_url = rtsp_url
        self.root = Path(recordings_root) / module_id
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.segment_seconds = segment_seconds
        self.overlap_seconds = overlap_seconds
        self.high_watermark_pct = high_watermark_pct
        self.low_watermark_pct = low_watermark_pct
        self.retention_days = retention_days

        self._stop = threading.Event()
        self._record_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        # Each entry: {"proc": Popen, "log_f": file, "path": Path}. Tracked as
        # dicts (not just proc/log_f) so cleanup can cross-reference which
        # segment files are still being written and never delete one of those.
        self._processes: list[dict] = []
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
            for entry in self._processes:
                if entry["proc"].poll() is None:
                    entry["proc"].terminate()
                entry["log_f"].close()
            self._processes = []
        # Explicit "stopped" event so the dashboard reflects a clean shutdown
        # immediately, rather than waiting for the last segment's start time
        # to just go stale.
        self.publish("recording", json.dumps({"active": False, "stopped": time.time()}))
        if self._record_thread:
            self._record_thread.join(timeout=5)
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)

    # -- recording --

    def _record_loop(self) -> None:
        # Epoch-aligned boundaries line up with wall-clock 5-minute marks in
        # any real-world timezone (all UTC offsets are multiples of 5 min).
        now = time.time()
        boundary = int(now // self.segment_seconds) * self.segment_seconds

        # Start recording immediately rather than waiting (up to
        # segment_seconds) for the next aligned mark — matters for how
        # quickly recording resumes after a restart/crash, not just for
        # testing. This first segment just runs shorter, ending at the same
        # aligned point every segment after it uses.
        first_end = boundary + self.segment_seconds + self.overlap_seconds
        self._launch_segment(boundary, duration=max(1, first_end - now))
        self._reap_finished()

        while not self._stop.is_set():
            boundary += self.segment_seconds
            now = time.time()
            if self._stop.wait(boundary - now):
                break
            self._launch_segment(boundary, duration=self.segment_seconds + self.overlap_seconds)
            self._reap_finished()

    def _launch_segment(self, boundary_epoch: float, duration: float) -> None:
        dt = datetime.fromtimestamp(boundary_epoch).astimezone()
        path = compute_segment_path(dt, self.root, self.segment_seconds)
        cmd = [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "warning",
            "-rtsp_transport", "tcp", "-timeout", "15000000",
            "-i", self.rtsp_url,
            "-t", str(max(1, round(duration))),
            "-vf", f"fps={self.fps}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{self.bitrate_kbps}k",
            "-maxrate", f"{self.bitrate_kbps}k",
            "-bufsize", f"{self.bitrate_kbps * 2}k",
            "-an",
            # Regular (non-fragmented) MP4: finalizes duration/seek metadata
            # properly once the segment completes, for reliable playback in
            # QuickTime/VLC/everything. (Fragmented MP4 was tried briefly to
            # allow scrubbing mid-recording, but it left files with no usable
            # duration even after completing, and QuickTime often couldn't
            # open them at all — not worth it given Live View already covers
            # the "watch it right now" case.)
            "-movflags", "+faststart",
            str(path),
        ]
        # Storage can disappear out from under us (drive unplugged) between
        # one segment and the next. That must never kill this thread — it's
        # the *only* thing that's supposed to notice and report "not
        # recording"; heartbeat and live view run independently in the same
        # process and shouldn't be affected by a storage problem at all. So
        # any failure here is caught, reported, and retried on the next
        # scheduled segment — never raised.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            log.info("recording segment -> %s", path)
            log_f = open(path.with_suffix(".log"), "w")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_f)
        except OSError:
            log.warning(
                "could not start recording segment %s — storage unavailable? "
                "will retry next scheduled segment",
                path, exc_info=True,
            )
            self.publish(
                "recording",
                json.dumps({"active": False, "error": "write_failed", "ts": time.time()}),
            )
            return
        with self._lock:
            self._processes.append({"proc": proc, "log_f": log_f, "path": path})
        self.publish(
            "recording",
            json.dumps({"active": True, "segment": str(path), "started": boundary_epoch}),
        )

    def _reap_finished(self) -> None:
        with self._lock:
            still_running = []
            for entry in self._processes:
                proc = entry["proc"]
                if proc.poll() is None:
                    still_running.append(entry)
                    continue
                entry["log_f"].close()
                if proc.returncode != 0:
                    log.warning(
                        "ffmpeg segment exited %s — see %s", proc.returncode, entry["path"].with_suffix(".log")
                    )
                    # Report immediately rather than waiting for the dashboard's
                    # staleness timeout — a non-zero exit (e.g. write I/O error
                    # from the drive disappearing mid-segment) is a strong
                    # signal recording actually stopped, not just delayed.
                    self.publish(
                        "recording",
                        json.dumps(
                            {
                                "active": False,
                                "error": "segment_failed",
                                "segment": str(entry["path"]),
                                "ts": time.time(),
                            }
                        ),
                    )
            self._processes = still_running

    def _active_paths(self) -> set[Path]:
        with self._lock:
            return {entry["path"] for entry in self._processes if entry["proc"].poll() is None}

    # -- cleanup --

    def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_cleanup()
            except Exception:
                log.exception("cleanup pass failed")
            # Storage can fill quickly on a small drive, so this runs far more
            # often than the old hourly date-based sweep did.
            if self._stop.wait(120):
                break

    def _run_cleanup(self) -> None:
        if not self.root.exists():
            return
        if self.retention_days is not None:
            self._retention_ceiling_cleanup()
        self._watermark_cleanup()

    def _retention_ceiling_cleanup(self) -> None:
        """Optional hard cutoff independent of free space (privacy/legal)."""
        cutoff = datetime.now().date() - timedelta(days=self.retention_days)
        for day_dir in sorted(d for d in self.root.iterdir() if d.is_dir()):
            try:
                day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                log.info(
                    "removing recordings older than retention_days=%d ceiling: %s",
                    self.retention_days, day_dir,
                )
                shutil.rmtree(day_dir, ignore_errors=True)

    def _watermark_cleanup(self) -> None:
        """Primary mechanism: keep disk usage between the two watermarks by
        deleting the single oldest completed segment file at a time."""
        usage = shutil.disk_usage(self.root)
        if usage.total == 0 or usage.used / usage.total * 100 < self.high_watermark_pct:
            return

        active = self._active_paths()
        log.warning(
            "disk usage %.1f%% >= high watermark %.1f%% — deleting oldest segments until <= %.1f%%",
            usage.used / usage.total * 100, self.high_watermark_pct, self.low_watermark_pct,
        )
        deleted_any = False
        for segment in self._iter_segments_oldest_first():
            usage = shutil.disk_usage(self.root)
            if usage.used / usage.total * 100 <= self.low_watermark_pct:
                break
            if segment in active:
                continue  # never delete a segment still being written
            self._delete_segment(segment)
            deleted_any = True
        else:
            log.warning("watermark cleanup ran out of deletable segments before reaching low watermark")

        if deleted_any:
            self._prune_empty_dirs()

    def _iter_segments_oldest_first(self):
        """Yield every completed segment file under self.root in chronological
        (oldest-first) order, based on the YYYY-MM-DD/HH/N.mp4 layout — not
        filesystem mtime."""
        if not self.root.exists():
            return
        for day_dir in sorted(d for d in self.root.iterdir() if d.is_dir()):
            for hour_dir in sorted(h for h in day_dir.iterdir() if h.is_dir()):
                files = sorted(
                    (f for f in hour_dir.iterdir() if f.suffix == ".mp4"),
                    key=lambda f: int(f.stem) if f.stem.isdigit() else 0,
                )
                yield from files

    def _delete_segment(self, segment: Path) -> None:
        log.info("watermark cleanup: removing oldest segment %s", segment)
        segment.unlink(missing_ok=True)
        segment.with_suffix(".log").unlink(missing_ok=True)

    def _prune_empty_dirs(self) -> None:
        if not self.root.exists():
            return
        for day_dir in sorted(d for d in self.root.iterdir() if d.is_dir()):
            for hour_dir in sorted(h for h in day_dir.iterdir() if h.is_dir()):
                if not any(hour_dir.iterdir()):
                    hour_dir.rmdir()
            if not any(day_dir.iterdir()):
                day_dir.rmdir()
