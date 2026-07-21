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


class DeviceRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleStatus] = {}

    def touch(self, module_id: str, module_type: str, online: bool = True) -> None:
        self._modules[module_id] = ModuleStatus(
            module_id=module_id, module_type=module_type, online=online
        )

    def all(self) -> list[ModuleStatus]:
        return list(self._modules.values())

    def get(self, module_id: str) -> ModuleStatus | None:
        return self._modules.get(module_id)
