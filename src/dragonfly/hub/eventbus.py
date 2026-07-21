"""Thin wrapper around the local MQTT broker.

Modules publish readings/events here; the hub subscribes to everything under
the `dragonfly/` prefix. Kept as a small seam so the hub's business logic
never touches paho-mqtt directly. See docs/architecture.md.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

TOPIC_PREFIX = "dragonfly"


class EventBus:
    def __init__(self, host: str = "localhost", port: int = 1883) -> None:
        self._host = host
        self._port = port
        self._client = mqtt.Client()
        self._client.on_message = self._on_message
        self._handlers: list[Callable[[str, bytes], Any]] = []

    def connect(self) -> None:
        self._client.connect(self._host, self._port)
        self._client.subscribe(f"{TOPIC_PREFIX}/#")

    def on_event(self, handler: Callable[[str, bytes], Any]) -> None:
        """Register a callback invoked as handler(topic, payload) for every message."""
        self._handlers.append(handler)

    def publish(self, module_id: str, subtopic: str, payload: bytes | str) -> None:
        self._client.publish(f"{TOPIC_PREFIX}/{module_id}/{subtopic}", payload)

    def loop_forever(self) -> None:
        self._client.loop_forever()

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        for handler in self._handlers:
            handler(msg.topic, msg.payload)
