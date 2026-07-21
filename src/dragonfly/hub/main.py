"""Entry point for the hub process (console script: `dragonfly-hub`).

Wires config -> event bus -> registry -> dashboard. Module drivers subscribe
themselves to the event bus as they're built out; none are wired in yet.
"""
from __future__ import annotations

import logging

from dragonfly.hub.config import load_config
from dragonfly.hub.eventbus import EventBus
from dragonfly.hub.registry import DeviceRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.hub")


def run() -> None:
    config = load_config()
    registry = DeviceRegistry()
    bus = EventBus(host=config.mqtt.host, port=config.mqtt.port)

    def on_event(topic: str, payload: bytes) -> None:
        # dragonfly/<module_id>/<subtopic>
        parts = topic.split("/")
        if len(parts) >= 2:
            registry.touch(module_id=parts[1], module_type="unknown")
        log.info("event topic=%s payload=%r", topic, payload)

    bus.on_event(on_event)
    bus.connect()

    log.info("dragonfly-hub starting up")
    # TODO: start the dashboard (uvicorn) alongside the event loop, e.g. via
    # a process/thread supervisor once dashboard/app.py has real routes.
    bus.loop_forever()


if __name__ == "__main__":
    run()
