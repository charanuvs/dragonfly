"""Camera capture + local recording module (stub).

TODO:
- Pick capture backend: picamera2 (Pi Camera Module) vs opencv VideoCapture
  (USB/IP cameras) depending on the hardware acquired.
- Continuous vs motion-triggered recording, retention policy.
- Write segments under <storage.recordings_path>/<module_id>/<date>/.
"""
from __future__ import annotations

from dragonfly.hub.eventbus import EventBus
from dragonfly.modules.base import SensorModule


class CameraModule(SensorModule):
    module_type = "camera"

    def __init__(self, module_id: str, bus: EventBus, recordings_path: str) -> None:
        super().__init__(module_id, bus)
        self.recordings_path = recordings_path

    def start(self) -> None:
        raise NotImplementedError("Camera capture backend not yet chosen/implemented.")

    def stop(self) -> None:
        pass
