"""Individual health checks used by the watchdog.

Each check is a small, side-effect-free function returning a plain dict, so
they're straightforward to unit test without a running system. The watchdog
loop (monitor.py) is what schedules them and reports the results.

Design note: these deliberately observe *externally* rather than trusting
anything the capture process says about itself. A process reporting its own
health can't report that it has died, and a recorder reporting "recording
started" doesn't prove bytes actually landed on disk — which is exactly the
failure mode that made recording status untrustworthy before this existed.
So: reachability is a real TCP connect, storage health is a real stat of the
mount, recording is verified by looking at actual files on disk, and capture
liveness comes from systemd rather than from capture itself.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from dragonfly.modules.camera.rtsp_camera import parse_rtsp_target


def check_camera(rtsp_url: str, timeout_s: float = 3.0) -> dict:
    """Is the camera reachable on its RTSP port right now?"""
    if "<camera-ip>" in rtsp_url or "<user>" in rtsp_url:
        return {
            "online": False,
            "host": "<unconfigured>",
            "port": 554,
            "error": "unconfigured_url",
        }
    try:
        host, port = parse_rtsp_target(rtsp_url)
    except ValueError as exc:
        return {"online": False, "error": str(exc)}
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return {"online": True, "host": host, "port": port}
    except OSError as exc:
        return {"online": False, "host": host, "port": port, "error": str(exc)}


def check_storage(mount_point: str | None, path: str, full_pct: float = 95.0) -> dict:
    """Is the recordings drive mounted, readable, and not full?

    'Readable' is checked with os.statvfs, which fails with EIO on a mount
    whose device was physically yanked — the case where os.path.ismount()
    still returns True and everything looks fine until a write is attempted.
    """
    result: dict = {"mount_point": mount_point, "path": path}

    if mount_point is not None:
        mounted = os.path.ismount(mount_point)
        result["mounted"] = mounted
        if not mounted:
            return {**result, "online": False, "healthy": False, "error": "not_mounted"}

    probe = mount_point or path
    try:
        stats = os.statvfs(probe)
    except OSError as exc:
        # Mounted but dead — the zombie-mount case. Reported distinctly from
        # "not mounted" because the remedy differs (needs umount -l first).
        return {**result, "online": False, "healthy": False, "error": f"unreadable: {exc}"}

    total = stats.f_blocks * stats.f_frsize
    free = stats.f_bavail * stats.f_frsize
    used = total - free
    used_pct = (used / total * 100) if total else 0.0
    full = used_pct >= full_pct

    return {
        **result,
        "online": not full,
        "healthy": True,
        "full": full,
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_pct": round(used_pct, 1),
    }


def check_recording(
    recordings_root: str,
    module_id: str,
    segment_seconds: int = 300,
    grace_s: float = 60.0,
) -> dict:
    """Verify recording by looking at the files actually on disk.

    This is the ground-truth check: rather than believing an "I started a
    segment" event, find the newest .mp4 and see whether it has been written
    to recently enough. A segment is rewritten continuously while active, so
    on a healthy recorder the newest file's mtime is never much older than
    now. Anything past segment_seconds + grace means writes have stopped,
    whatever else the system claims.
    """
    root = Path(recordings_root) / module_id
    stale_after = segment_seconds + grace_s

    try:
        newest: Path | None = None
        newest_mtime = 0.0
        for mp4 in root.rglob("*.mp4"):
            try:
                mtime = mp4.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = mp4, mtime
    except OSError as exc:
        return {"active": False, "error": f"unreadable: {exc}", "root": str(root)}

    if newest is None:
        return {"active": False, "error": "no_recordings", "root": str(root)}

    age = time.time() - newest_mtime
    return {
        "active": age <= stale_after,
        "newest_segment": str(newest),
        "newest_age_s": round(age, 1),
        "newest_size_bytes": newest.stat().st_size if newest.exists() else 0,
        "stale_after_s": stale_after,
    }


def check_service(service_name: str) -> dict:
    """Is a background service currently active?

    External observation rather than the process's own heartbeat, so it stays
    correct even if the process is hard-killed, wedged, or never started.
    Checks systemctl on Linux; falls back to launchctl/pgrep on macOS.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = proc.stdout.strip() or proc.stderr.strip()
        return {"online": proc.returncode == 0, "service": service_name, "state": state}
    except FileNotFoundError:
        # systemctl is not available (e.g. macOS / Darwin or systems without systemd)
        if sys.platform == "darwin":
            labels = [service_name, f"com.{service_name}", service_name.replace("-", ".")]
            for label in labels:
                try:
                    p = subprocess.run(
                        ["launchctl", "list", label],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if p.returncode == 0:
                        is_running = '"PID"' in p.stdout
                        return {
                            "online": is_running,
                            "service": label,
                            "state": "running" if is_running else "loaded",
                        }
                except Exception:
                    pass

        # Generic fallback: check if process is running via pgrep
        try:
            p = subprocess.run(
                ["pgrep", "-f", service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_active = p.returncode == 0
            return {
                "online": is_active,
                "service": service_name,
                "state": "active" if is_active else "inactive",
            }
        except Exception:
            return {"online": False, "service": service_name, "error": "service_manager_not_found"}
    except subprocess.SubprocessError as exc:
        return {"online": False, "service": service_name, "error": str(exc)}


def repair_storage(command: str, timeout_s: float = 60.0) -> dict:
    """Attempt to repair the recordings mount (via sudo).

    The heavy lifting is in the shell script this points at — it clears a
    dead mount with `umount -l` and re-runs `mount -a`. It needs root, and
    the watchdog runs unprivileged inside portal, hence sudo with a sudoers
    rule scoped to exactly this one command. The installer places the
    script root-owned under /usr/local/sbin deliberately: if it lived in the
    user-writable repo checkout, a NOPASSWD sudo rule pointing at it would
    effectively be passwordless root for that user.
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", command],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": (proc.stdout + proc.stderr).strip()[-400:],
        }
    except FileNotFoundError:
        return {"ok": False, "error": "sudo_or_command_not_found"}
    except subprocess.SubprocessError as exc:
        return {"ok": False, "error": str(exc)}


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


__all__ = [
    "check_camera",
    "check_storage",
    "check_recording",
    "check_service",
    "repair_storage",
    "human_bytes",
]
