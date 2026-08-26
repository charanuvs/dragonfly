"""Heartbeat-only monitor for an RTSP/ONVIF network camera.

This deliberately does not pull video yet — it just confirms the camera is
reachable on its RTSP port on an interval and publishes online/offline over
the event bus, so the dashboard can show "is OW up right now". Actual stream
capture/recording is a separate follow-up; see docs/architecture.md.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from urllib.parse import urlsplit

from dragonfly.hub.eventbus import EventBus
from dragonfly.modules.base import SensorModule

log = logging.getLogger("dragonfly.modules.camera.rtsp")


def parse_rtsp_target(rtsp_url: str) -> tuple[str, int]:
    """Extract (host, port) from an rtsp:// URL, defaulting to port 554."""
    parts = urlsplit(rtsp_url)
    if not parts.hostname:
        raise ValueError(f"Could not parse host from RTSP URL: {rtsp_url!r}")
    return parts.hostname, parts.port or 554


class RtspCameraModule(SensorModule):
    module_type = "camera"

    def __init__(
        self,
        module_id: str,
        bus: EventBus,
        rtsp_url: str,
        poll_interval_s: float = 15.0,
        timeout_s: float = 3.0,
    ) -> None:
        super().__init__(module_id, bus)
        self.host, self.port = parse_rtsp_target(rtsp_url)
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"heartbeat-{self.module_id}"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.timeout_s + 1)

    def _run(self) -> None:
        while not self._stop.is_set():
            online = self._check_once()
            payload = json.dumps({"online": online, "type": self.module_type, "ts": time.time()})
            # Retained: this is state, so a portal starting later should learn
            # online/offline immediately rather than after up to
            # poll_interval_s of showing nothing.
            self.publish("heartbeat", payload, retain=True)
            log.info("%s heartbeat: %s", self.module_id, "online" if online else "offline")
            self._stop.wait(self.poll_interval_s)

    def _check_once(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_s):
                return True
        except OSError:
            return False
