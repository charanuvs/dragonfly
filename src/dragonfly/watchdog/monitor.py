"""The watchdog: periodic health monitoring, independent of capture.

Runs as a background thread inside the *portal* process rather than inside
capture, deliberately. Monitoring that lives inside the thing it monitors
can't report that thing being dead — capture's old self-reported heartbeat
went silent when capture died, which the dashboard could only infer from
staleness. Running here, in a separate process with its own lifecycle, the
watchdog can state plainly "capture is down".

It owns four checks (see checks.py):

1. Camera reachable (TCP connect to the RTSP port). Moved here out of
   capture — capture doesn't need a heartbeat to do its job, and this is
   monitoring, so it belongs with the monitoring.
2. Storage mounted, readable, and not full.
3. Recording actually happening — verified against files on disk, not by
   trusting an event that said a segment started.
4. The capture process itself running, per systemd.

Results go straight into the DeviceRegistry the dashboard renders from, and
are also published retained over MQTT so anything else that cares (or a
human with mosquitto_sub) sees the same state.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from dragonfly.hub.config import HubConfig
from dragonfly.hub.eventbus import EventBus
from dragonfly.hub.registry import DeviceRegistry
from dragonfly.watchdog.checks import (
    check_camera,
    check_recording,
    check_service,
    check_storage,
)

log = logging.getLogger("dragonfly.watchdog")

STORAGE_MODULE_ID = "_storage"
CAPTURE_MODULE_ID = "_capture"


class Watchdog:
    def __init__(
        self,
        config: HubConfig,
        registry: DeviceRegistry,
        bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.bus = bus
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        interval = self.config.watchdog.interval_s
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # A failing check must never kill the watchdog — a dead
                # watchdog is worse than a wrong reading, since everything
                # downstream would silently freeze at its last known state.
                log.exception("watchdog pass failed")
            if self._stop.wait(interval):
                break

    def run_once(self) -> None:
        """One full pass of every check. Separated from the loop for testing."""
        now = time.time()

        capture = check_service(self.config.watchdog.capture_service)
        self._report(CAPTURE_MODULE_ID, "capture_process", capture, now)

        storage = check_storage(
            self.config.storage.mount_point,
            self.config.storage.recordings_path,
            full_pct=self.config.watchdog.storage_full_pct,
        )
        self._report(STORAGE_MODULE_ID, "storage", storage, now)

        for cam in self.config.cameras:
            camera = check_camera(cam.rtsp_url, timeout_s=cam.timeout_s)
            self._report(cam.id, "camera", camera, now)

            if not cam.record:
                continue
            # Only meaningful if storage is actually readable; otherwise the
            # scan itself would error and we'd report "no recordings" for
            # what is really a storage problem, already reported above.
            if not storage.get("healthy"):
                self.registry.touch_recording(
                    cam.id, active=False, segment=None, ts=now
                )
                continue

            recording = check_recording(
                self.config.storage.recordings_path,
                cam.id,
                segment_seconds=cam.segment_seconds,
                grace_s=self.config.watchdog.recording_grace_s,
            )
            self.registry.touch_recording(
                cam.id,
                active=bool(recording.get("active")),
                segment=recording.get("newest_segment"),
                ts=now,
            )
            self._publish(cam.id, "recording_verified", recording)

    def _report(self, module_id: str, module_type: str, result: dict, now: float) -> None:
        self.registry.touch(
            module_id,
            module_type,
            online=bool(result.get("online")),
            last_seen=now,
            details={k: v for k, v in result.items() if k != "online"},
        )
        self._publish(module_id, "heartbeat", {**result, "type": module_type, "ts": now})

    def _publish(self, module_id: str, subtopic: str, payload: dict) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(module_id, subtopic, json.dumps(payload), retain=True)
        except Exception:
            log.exception("failed publishing %s/%s", module_id, subtopic)
