"""Common interface every sensor/camera module implements.

The hub only ever talks to modules through this interface (indirectly, via
the event bus) so adding a new sensor type never requires touching hub code.
See docs/architecture.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from dragonfly.hub.eventbus import EventBus


class SensorModule(ABC):
    module_type: str = "unspecified"

    def __init__(self, module_id: str, bus: EventBus) -> None:
        self.module_id = module_id
        self.bus = bus

    @abstractmethod
    def start(self) -> None:
        """Begin reading hardware / listening, publishing events via self.bus."""

    @abstractmethod
    def stop(self) -> None:
        """Cleanly release hardware resources."""

    def publish(self, subtopic: str, payload: bytes | str) -> None:
        self.bus.publish(self.module_id, subtopic, payload)
