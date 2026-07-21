"""Load and validate the hub's deployment config.

Per-deployment config lives at config/dragonfly.yaml (not committed — see
config/dragonfly.example.yaml for the template and .gitignore). This module
is intentionally small right now; it grows as real modules are added.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class MqttConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883


class StorageConfig(BaseModel):
    recordings_path: str = "/mnt/dragonfly-hdd/recordings"
    database_path: str = "/mnt/dragonfly-hdd/dragonfly.db"


class DashboardConfig(BaseModel):
    bind_host: str = "0.0.0.0"  # bound to the LAN interface only; see docs/network.md
    port: int = 8000


class HubConfig(BaseModel):
    mqtt: MqttConfig = MqttConfig()
    storage: StorageConfig = StorageConfig()
    dashboard: DashboardConfig = DashboardConfig()


def load_config(path: str | Path = "config/dragonfly.yaml") -> HubConfig:
    path = Path(path)
    if not path.exists():
        # Fall back to defaults so the hub can boot in a fresh dev environment.
        return HubConfig()
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    return HubConfig(**raw)
