"""On-demand live HLS view of a camera's HD (main) stream.

Unlike the recorder (always running, opt-in via config) and the heartbeat
(always running, cheap TCP check), the live view only starts an ffmpeg
process when someone actually opens the dashboard's live page, and stops it
again after a period with no viewers. An HD stream is heavier than the other
two both on the Pi and on the camera's limited concurrent-connection budget,
so it isn't worth keeping alive when nobody's watching.

Uses `-c:v copy` (remux, not re-encode) into short HLS segments — cheap on
CPU since it's not touching the video data, just repackaging it.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("dragonfly.dashboard.live")

IDLE_TIMEOUT_S = 60
SEGMENT_S = 2
PLAYLIST_SIZE = 6


class _CameraStream:
    def __init__(self, rtsp_url: str, out_dir: Path) -> None:
        self.rtsp_url = rtsp_url
        self.out_dir = out_dir
        self.process: subprocess.Popen | None = None
        self.log_f = None
        self.last_access = time.time()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class LiveStreamManager:
    def __init__(self, cameras: dict[str, str]) -> None:
        """cameras: {module_id: rtsp_url} — the stream to use for live view."""
        self._base_dir = Path(tempfile.mkdtemp(prefix="dragonfly-live-"))
        self._streams = {
            module_id: _CameraStream(rtsp_url, self._base_dir / module_id)
            for module_id, rtsp_url in cameras.items()
        }
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None

    def start_reaper(self) -> None:
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True, name="live-reaper")
        self._reaper.start()

    def stop_all(self) -> None:
        self._stop.set()
        with self._lock:
            for stream in self._streams.values():
                self._stop_stream(stream)
        shutil.rmtree(self._base_dir, ignore_errors=True)

    def has(self, module_id: str) -> bool:
        return module_id in self._streams

    def ensure_running(self, module_id: str) -> Path:
        """Start the stream if it isn't already running; returns its playlist path."""
        stream = self._streams[module_id]
        with self._lock:
            stream.last_access = time.time()
            if not stream.is_running():
                self._start_stream(stream)
        return stream.out_dir / "index.m3u8"

    def _start_stream(self, stream: _CameraStream) -> None:
        stream.out_dir.mkdir(parents=True, exist_ok=True)
        for f in stream.out_dir.glob("*"):
            f.unlink(missing_ok=True)
        cmd = [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "warning",
            "-rtsp_transport", "tcp", "-timeout", "15000000",
            "-i", stream.rtsp_url,
            "-c:v", "copy", "-an",
            "-f", "hls",
            "-hls_time", str(SEGMENT_S),
            "-hls_list_size", str(PLAYLIST_SIZE),
            "-hls_flags", "delete_segments+append_list",
            str(stream.out_dir / "index.m3u8"),
        ]
        log.info("starting live stream -> %s", stream.out_dir)
        stream.log_f = open(stream.out_dir / "ffmpeg.log", "w")
        stream.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stream.log_f)

    def _stop_stream(self, stream: _CameraStream) -> None:
        if stream.process and stream.process.poll() is None:
            stream.process.terminate()
            try:
                stream.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stream.process.kill()
        if stream.log_f:
            stream.log_f.close()
            stream.log_f = None
        stream.process = None

    def _reap_loop(self) -> None:
        while not self._stop.wait(15):
            with self._lock:
                for stream in self._streams.values():
                    if stream.is_running() and time.time() - stream.last_access > IDLE_TIMEOUT_S:
                        log.info("no viewers for %ds, stopping live stream", IDLE_TIMEOUT_S)
                        self._stop_stream(stream)
