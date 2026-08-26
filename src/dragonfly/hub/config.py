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


class CameraConfig(BaseModel):
    id: str
    name: str
    rtsp_url: str  # keep this only in config/dragonfly.yaml (gitignored) — it carries credentials
    poll_interval_s: float = 15.0
    timeout_s: float = 3.0

    # Recording (opt-in per camera; see docs/recording.md for the storage math
    # behind these defaults).
    record: bool = False
    # Point this at a lower-res substream if your camera exposes one, to save
    # storage — defaults to rtsp_url (the same stream used for heartbeat) if unset.
    recording_rtsp_url: str | None = None
    fps: int = 10
    bitrate_kbps: int = 200
    segment_seconds: int = 300
    overlap_seconds: int = 10

    # Storage-usage-based cleanup (primary mechanism): once disk usage hits
    # high_watermark_pct, the oldest completed segment files are deleted one
    # at a time until usage drops back to low_watermark_pct. Self-adjusting —
    # retention in days falls out of whatever bitrate/fps you've configured,
    # rather than needing to be hand-tuned. See docs/recording.md.
    high_watermark_pct: float = 90.0
    low_watermark_pct: float = 80.0
    # Optional hard ceiling independent of free space (e.g. for a privacy/
    # legal reason to never keep footage past N days even if there's room).
    # None (default) disables this — storage usage is the only thing that
    # drives deletion.
    retention_days: int | None = None


class HubConfig(BaseModel):
    mqtt: MqttConfig = MqttConfig()
    storage: StorageConfig = StorageConfig()
    dashboard: DashboardConfig = DashboardConfig()
    cameras: list[CameraConfig] = []


def load_config(path: str | Path = "config/dragonfly.yaml") -> HubConfig:
    path = Path(path)
    if not path.exists():
        # Fall back to defaults so the hub can boot in a fresh dev environment.
        return HubConfig()
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    return HubConfig(**raw)
