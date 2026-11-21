"""Entry point for the dfly-dashboard UI service."""
from __future__ import annotations

import json
import logging
import time

import uvicorn

from dragonfly.common.config import load_config
from dragonfly.common.constants import DEFAULT_LIVE_DIR, DEVICE_API_URL
from dragonfly.common.eventbus import EventBus
from dragonfly.common.registry import DeviceRegistry
from dragonfly.services.dfly_dashboard.app import app as dashboard_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.dashboard")


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
                details={
                    key: value
                    for key, value in data.items()
                    if key not in {"type", "online", "ts"}
                },
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
        elif subtopic == "recording_verified":
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("bad verified-recording payload on %s: %r", topic, payload)
                return
            registry.touch_recording(
                module_id,
                active=bool(data.get("active")),
                segment=data.get("newest_segment"),
                ts=float(data.get("ts", time.time())),
            )

    bus.on_event(on_event)
    bus.connect()
    bus.loop_start()

    dashboard_app.state.registry = registry
    dashboard_app.state.camera_config = {cam.id: cam for cam in config.cameras}
    dashboard_app.state.storage_path = config.storage.recordings_path
    dashboard_app.state.recordings_path = config.storage.recordings_path
    dashboard_app.state.storage_mount_point = config.storage.mount_point
    dashboard_app.state.event_bus = bus
    dashboard_app.state.live_dir = DEFAULT_LIVE_DIR
    dashboard_app.state.kasa_switch_config = config.kasa_switch
    dashboard_app.state.device_api_url = DEVICE_API_URL
    dashboard_app.state.camera_config_loader = load_config

    log.info(
        "dfly-dashboard starting up on %s:%d",
        config.dashboard.bind_host,
        config.dashboard.port,
    )
    tls_options = {}
    if config.dashboard.tls_certfile or config.dashboard.tls_keyfile:
        if not config.dashboard.tls_certfile or not config.dashboard.tls_keyfile:
            raise ValueError("Both dashboard.tls_certfile and dashboard.tls_keyfile are required for HTTPS")
        tls_options = {
            "ssl_certfile": config.dashboard.tls_certfile,
            "ssl_keyfile": config.dashboard.tls_keyfile,
        }
    try:
        uvicorn.run(
            dashboard_app,
            host=config.dashboard.bind_host,
            port=config.dashboard.port,
            **tls_options,
        )
    finally:
        bus.loop_stop()


if __name__ == "__main__":
    run()
