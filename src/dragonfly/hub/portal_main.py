"""Entry point for the portal process (console script: `dragonfly-portal`).

Serves the web dashboard. Deliberately has no direct reference to the
capture process's objects (heartbeat modules, recorder, live-stream
manager) — the two processes are independent on purpose (see
docs/architecture.md), coordinating only over MQTT:

- Portal builds its own DeviceRegistry by independently subscribing to the
  same `dragonfly/#` topics capture publishes to (heartbeat events).
- For live view, portal publishes a `live_start_request` and reads the
  resulting HLS files straight off disk from the well-known shared
  directory capture writes them to (dashboard/live.py's DEFAULT_LIVE_DIR).

Restarting this process (e.g. after every dashboard UI tweak) never
interrupts a recording or a live stream in progress in the capture process.
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
            )
        elif subtopic == "recording":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("bad recording payload on %s: %r", topic, payload)
                return
            registry.touch_recording(
                module_id,
                active=bool(data.get("active", True)),
                segment=data.get("segment"),
                ts=float(data.get("started", data.get("stopped", time.time()))),
            )

    bus.on_event(on_event)
    bus.connect()
    bus.loop_start()

    dashboard_app.state.registry = registry
    dashboard_app.state.camera_config = {cam.id: cam for cam in config.cameras}
    dashboard_app.state.storage_path = config.storage.recordings_path
    dashboard_app.state.event_bus = bus
    dashboard_app.state.live_dir = DEFAULT_LIVE_DIR

    log.info(
        "dragonfly-portal starting up on %s:%d",
        config.dashboard.bind_host,
        config.dashboard.port,
    )
    try:
        uvicorn.run(dashboard_app, host=config.dashboard.bind_host, port=config.dashboard.port)
    finally:
        bus.loop_stop()


if __name__ == "__main__":
    run()
