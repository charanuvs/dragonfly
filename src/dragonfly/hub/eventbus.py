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
        # paho-mqtt 2.x requires an explicit callback API version; VERSION2 is
        # the current (non-deprecated) one and doesn't change our on_message
        # signature (client, userdata, msg) from VERSION1.
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._handlers: list[Callable[[str, bytes], Any]] = []

    def connect(self) -> None:
        self._client.connect(self._host, self._port)

    def _on_connect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, _reason_code: Any, _properties: Any = None
    ) -> None:
        # Subscribing here (rather than right after connect()) means we
        # automatically re-subscribe after any reconnect, not just the first one.
        client.subscribe(f"{TOPIC_PREFIX}/#")

    def on_event(self, handler: Callable[[str, bytes], Any]) -> None:
        """Register a callback invoked as handler(topic, payload) for every message."""
        self._handlers.append(handler)

    def publish(
        self, module_id: str, subtopic: str, payload: bytes | str, retain: bool = False
    ) -> None:
        """Publish an event.

        `retain=True` asks the broker to keep this as the last-known value for
        the topic and replay it to any future subscriber the moment it
        subscribes. Use it for *state* ("the camera is online", "recording is
        active") — without it, a process that starts later (notably the portal,
        which rebuilds its whole registry from these events) knows nothing
        until the next event happens to fire, which for recording events can be
        a full segment_seconds away. Don't use it for one-off *commands* like
        live_start_request, where a replayed message on reconnect would
        re-trigger an action nobody asked for.
        """
        self._client.publish(f"{TOPIC_PREFIX}/{module_id}/{subtopic}", payload, retain=retain)

    def loop_forever(self) -> None:
        self._client.loop_forever()

    def loop_start(self) -> None:
        """Run the network loop in a background thread (non-blocking)."""
        self._client.loop_start()

    def loop_stop(self) -> None:
        self._client.loop_stop()

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        for handler in self._handlers:
            handler(msg.topic, msg.payload)
