"""System resource stats (CPU/mem/disk) for the dashboard.

Uses psutil rather than hand-parsing /proc — accurate CPU% specifically
needs a sampling delta, which psutil handles correctly (and portably, in
case this is ever run for local dev on the Mac too).
"""
from __future__ import annotations

import shutil
import socket
import time

import psutil

# Prime the CPU-percent sampler at import time. psutil.cpu_percent() compares
# against the last call; without this, the very first real reading would be
# a meaningless "average since boot" instead of "since 5s ago".
psutil.cpu_percent(interval=None)


def get_system_stats(disk_path: str) -> dict:
    mem = psutil.virtual_memory()
    stats = {
        "hostname": socket.gethostname(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "mem_total": mem.total,
        "mem_used": mem.total - mem.available,
        "mem_percent": mem.percent,
        "uptime_s": time.time() - psutil.boot_time(),
    }
    try:
        disk = shutil.disk_usage(disk_path)
        stats.update(
            {
                "disk_path": disk_path,
                "disk_total": disk.total,
                "disk_used": disk.used,
                "disk_percent": round(disk.used / disk.total * 100, 1) if disk.total else 0.0,
                "disk_ok": True,
            }
        )
    except OSError:
        stats.update({"disk_path": disk_path, "disk_ok": False})
    return stats
