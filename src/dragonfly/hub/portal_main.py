"""Entry point for the portal process (console script: `dragonfly-portal`).

Serves the web dashboard, and hosts the watchdog. Deliberately has no direct
reference to the capture process's objects (recorder, live-stream manager) —
the two processes are independent on purpose (see docs/architecture.md),
coordinating only over MQTT:

- Portal builds its own DeviceRegistry from the watchdog's checks plus the
  `dragonfly/#` events capture publishes.
- For live view, portal publishes a `live_start_request` and reads the
  resulting HLS files straight off disk from the well-known shared
  directory capture writes them to (dashboard/live.py's DEFAULT_LIVE_DIR).

The watchdog runs here rather than in capture on purpose: a component can't
report its own death, so the thing checking whether capture is alive has to
live outside capture. Portal already runs continuously and independently, so
it's the natural host — no third service to deploy.

Restarting this process (e.g. after every dashboard UI tweak) never
interrupts a recording or a live stream in progress in the capture process;
it only pauses monitoring for the second or two it takes to come back.
"""
from __future__ import annotations

import json
import logging
import time

import uvicorn

from dragonfly.dashboard.app import app as dashboard_app
from dragonfly.dashboard.live import DEFAULT_LIVE_DIR
from dragonfly.hub.config import load_config
from dragonfly.hub.eventbus import EventBus
from dragonfly.hub.registry import DeviceRegistry
from dragonfly.watchdog.monitor import Watchdog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.portal")


def run() -> None:
    config = load_config()
    registry = DeviceRegistry()
    bus = EventBus(host=config.mqtt.host, port=config.mqtt.port)

    def on_event(topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 3:
            return
        module_id, subtopic = parts[1], parts[2]
        if subtopic == "heartbeat":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("bad heartbeat payload on %s: %r", topic, payload)
                return
            registry.touch(
                module_id,
                data.get("type", "unknown"),
                online=bool(data.get("online", False)),
                # From the payload, not receive time — these are retained, so
                # this may be a replayed message that's actually old.
                last_seen=data.get("ts"),
            )
        elif subtopic == "recording":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("bad recording payload on %s: %r", topic, payload)
                return
            # Capture's own report of what it just tried to do. Deliberately
            # does NOT set recording_active — that's owned by the watchdog,
            # which verifies against files on disk. Capture saying "I started
            # a segment" isn't proof bytes landed; keeping the two separate is
            # the point. What capture uniquely knows is *why* something
            # failed, so that detail is what's kept here.
            existing = registry.get(module_id)
            details = dict(existing.details) if existing else {}
            details["capture_report"] = data.get("error") or (
                "recording" if data.get("active") else "stopped"
            )
            registry.touch(
                module_id,
                existing.module_type if existing else "camera",
                online=existing.online if existing else False,
                last_seen=existing.last_seen if existing else time.time(),
                details=details,
            )

    bus.on_event(on_event)
    bus.connect()
    bus.loop_start()

    dashboard_app.state.registry = registry
    dashboard_app.state.camera_config = {cam.id: cam for cam in config.cameras}
    dashboard_app.state.storage_path = config.storage.recordings_path
    dashboard_app.state.event_bus = bus
    dashboard_app.state.live_dir = DEFAULT_LIVE_DIR

    # Health monitoring lives here, not in capture — see watchdog/monitor.py.
    watchdog = Watchdog(config, registry, bus)
    watchdog.start()

    log.info(
        "dragonfly-portal starting up on %s:%d (watchdog every %.0fs)",
        config.dashboard.bind_host,
        config.dashboard.port,
        config.watchdog.interval_s,
    )
    try:
        uvicorn.run(dashboard_app, host=config.dashboard.bind_host, port=config.dashboard.port)
    finally:
        bus.loop_stop()


if __name__ == "__main__":
    run()
