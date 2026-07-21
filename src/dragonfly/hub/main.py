"""Entry point for the hub process (console script: `dragonfly-hub`).

Runs as a single process on the Pi ("Phila"): wires config -> event bus ->
registry -> camera heartbeat modules -> dashboard. The dashboard (FastAPI,
served over uvicorn) and the MQTT event loop share one in-memory
DeviceRegistry, so no separate process/IPC is needed for the dashboard to see
live status.
"""
from __future__ import annotations

import json
import logging

import uvicorn

from dragonfly.dashboard.app import app as dashboard_app
from dragonfly.hub.config import load_config
from dragonfly.hub.eventbus import EventBus
from dragonfly.hub.registry import DeviceRegistry
from dragonfly.modules.camera.rtsp_camera import RtspCameraModule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.hub")


def run() -> None:
    config = load_config()
    registry = DeviceRegistry()
    bus = EventBus(host=config.mqtt.host, port=config.mqtt.port)

    def on_event(topic: str, payload: bytes) -> None:
        # dragonfly/<module_id>/<subtopic>
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
        log.info("event topic=%s payload=%r", topic, payload)

    bus.on_event(on_event)
    bus.connect()
    bus.loop_start()

    camera_modules = [
        RtspCameraModule(
            module_id=cam.id,
            bus=bus,
            rtsp_url=cam.rtsp_url,
            poll_interval_s=cam.poll_interval_s,
            timeout_s=cam.timeout_s,
        )
        for cam in config.cameras
    ]
    for mod in camera_modules:
        mod.start()

    # Hand the shared registry + camera metadata to the dashboard before it
    # starts serving requests.
    dashboard_app.state.registry = registry
    dashboard_app.state.camera_config = {cam.id: cam for cam in config.cameras}

    log.info(
        "dragonfly-hub starting up on %s:%d (%d camera module(s))",
        config.dashboard.bind_host,
        config.dashboard.port,
        len(camera_modules),
    )
    try:
        uvicorn.run(dashboard_app, host=config.dashboard.bind_host, port=config.dashboard.port)
    finally:
        for mod in camera_modules:
            mod.stop()
        bus.loop_stop()


if __name__ == "__main__":
    run()
