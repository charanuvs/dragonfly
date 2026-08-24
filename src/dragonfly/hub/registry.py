"""In-memory (for now) registry of every known module and its last-seen state.

This is the hub's source of truth for "what devices exist." Persisting it to
the database is a near-term TODO once storage.py exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ModuleStatus:
    module_id: str
    module_type: str
    last_seen: float = field(default_factory=time.time)
    online: bool = True

    # Recording status, populated from a separate `recording` MQTT subtopic
    # (published by SegmentedRecorder) — independent of heartbeat, since a
    # camera can be online but not configured to record, or vice versa.
    recording_active: bool = False
    recording_last_segment: str | None = None
    recording_last_event: float | None = None


class DeviceRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleStatus] = {}

    def touch(self, module_id: str, module_type: str, online: bool = True) -> None:
        existing = self._modules.get(module_id)
        status = ModuleStatus(module_id=module_id, module_type=module_type, online=online)
        if existing is not None:
            # Preserve recording state — heartbeat and recording are reported
            # on independent schedules, so a heartbeat update shouldn't wipe
            # out what we last heard about recording.
            status.recording_active = existing.recording_active
            status.recording_last_segment = existing.recording_last_segment
            status.recording_last_event = existing.recording_last_event
        self._modules[module_id] = status

    def touch_recording(self, module_id: str, active: bool, segment: str | None, ts: float) -> None:
        existing = self._modules.get(module_id)
        if existing is None:
            # Recording events can arrive before this module's first heartbeat
            # (or if heartbeat is offline/misconfigured) — create a stub entry
            # rather than dropping the event.
            existing = ModuleStatus(module_id=module_id, module_type="camera", online=False)
            self._modules[module_id] = existing
        existing.recording_active = active
        existing.recording_last_segment = segment
        existing.recording_last_event = ts

    def all(self) -> list[ModuleStatus]:
        return list(self._modules.values())

    def get(self, module_id: str) -> ModuleStatus | None:
        return self._modules.get(module_id)
