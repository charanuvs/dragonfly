"""Load and validate the hub's deployment config.

Per-deployment config lives at config/dragonfly.yaml (not committed — see
config/dragonfly.example.yaml for the template and .gitignore). This module
is intentionally small right now; it grows as real modules are added.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class MqttConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883


class StorageConfig(BaseModel):
    recordings_path: str = "/mnt/dragonfly-hdd/recordings"
    database_path: str = "/mnt/dragonfly-hdd/dragonfly.db"
    # The mount point recordings_path is expected to live under. The recorder
    # checks os.path.ismount(mount_point) before writing each segment — if
    # the drive isn't actually mounted there, mkdir() would otherwise happily
    # (and silently) create directories on the underlying root filesystem
    # instead of erroring, which would mean recordings quietly go to the SD
    # card instead of the external drive. Set to None to skip this check.
    mount_point: str | None = "/mnt/dragonfly-hdd"


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


class WatchdogConfig(BaseModel):
    """Health monitoring, run by the portal process (see watchdog/monitor.py)."""

    interval_s: float = 15.0
    # Checked with `systemctl is-active` — external observation, so it stays
    # correct even if capture is hard-killed or never started.
    capture_service: str = "dragonfly-capture"
    # Storage counts as unhealthy at/above this usage. Above the recorder's
    # high_watermark_pct (90) so normal cleanup churn doesn't read as a fault.
    storage_full_pct: float = 95.0
    # How far past a segment's expected duration the newest file on disk may
    # be before recording is judged stopped.
    recording_grace_s: float = 60.0

    # When storage is found unmounted or unreadable, try to repair it by
    # running this (via sudo — see deploy/install.sh, which installs it
    # root-owned in /usr/local/sbin with a narrowly-scoped sudoers rule).
    # Set repair_storage: false to disable and handle mounts manually.
    repair_storage: bool = True
    repair_command: str = "/usr/local/sbin/dragonfly-mount-repair"
    # Don't hammer it — a drive that's genuinely absent can't be repaired,
    # and retrying every pass would just spam logs and spin up processes.
    repair_min_interval_s: float = 60.0


class HubConfig(BaseModel):
    mqtt: MqttConfig = MqttConfig()
    storage: StorageConfig = StorageConfig()
    dashboard: DashboardConfig = DashboardConfig()
    watchdog: WatchdogConfig = WatchdogConfig()
    cameras: list[CameraConfig] = []


def load_config(path: str | Path | None = None) -> HubConfig:
    if path is not None:
        target_path = Path(path)
    elif "DRAGONFLY_CONFIG" in os.environ:
        target_path = Path(os.environ["DRAGONFLY_CONFIG"])
    elif Path("/etc/dragonfly/dragonfly.yaml").exists():
        target_path = Path("/etc/dragonfly/dragonfly.yaml")
    elif (Path.home() / ".config/dragonfly/dragonfly.yaml").exists():
        target_path = Path.home() / ".config/dragonfly/dragonfly.yaml"
    elif Path("config/dragonfly.yaml").exists():
        target_path = Path("config/dragonfly.yaml")
    else:
        # Fall back to defaults so the hub can boot in a fresh dev environment.
        return HubConfig()

    if not target_path.exists():
        return HubConfig()
    with target_path.open() as f:
        raw = yaml.safe_load(f) or {}
    return HubConfig(**raw)
