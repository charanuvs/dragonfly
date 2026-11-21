"""Monitor Reolink motion markers and AI people detections."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from reolink_aio.api import Host

from dragonfly.common.recordings import compute_segment_path

log = logging.getLogger(__name__)


def append_motion_marker(
    camera_id: str,
    recordings_root: str | Path,
    segment_seconds: int,
    timestamp: float,
    mount_point: str | None = None,
) -> Path | None:
    if mount_point is not None and not os.path.ismount(mount_point):
        return None

    segment = compute_segment_path(
        datetime.fromtimestamp(timestamp).astimezone(),
        Path(recordings_root) / camera_id,
        segment_seconds,
    )
    if not segment.is_file():
        return None

    marker = segment.with_suffix(".motion.jsonl")
    try:
        with marker.open("a", encoding="utf-8") as marker_file:
            marker_file.write(json.dumps({"ts": timestamp}) + "\n")
    except OSError:
        log.warning("Could not save motion marker for %s", segment, exc_info=True)
        return None
    return segment


async def monitor_motion(
    camera_id: str,
    camera_name: str,
    rtsp_url: str,
    recordings_root: str,
    segment_seconds: int,
    mount_point: str | None,
    alerts,
    channel: int = 0,
    poll_interval_s: float = 1.0,
) -> None:
    parts = urlsplit(rtsp_url)
    if parts.hostname is None or parts.username is None or parts.password is None:
        log.error("Motion monitoring for %s requires camera credentials", camera_id)
        return

    while True:
        host = Host(
            parts.hostname,
            unquote(parts.username),
            unquote(parts.password),
            port=80,
            use_https=False,
            protocol="rtsp",
        )
        try:
            await host.get_host_data()
            motion_supported = host.supported(channel, "motion_detection")
            people_supported = host.ai_supported(channel, "people")
            if not motion_supported:
                log.warning("Camera %s does not support motion markers", camera_id)
            if not people_supported:
                log.warning("Camera %s does not support AI people detection", camera_id)
            if not motion_supported and not people_supported:
                return

            previous_motion_state = None
            previous_people_state = None
            log.info("Motion and AI people monitoring started for camera %s", camera_id)
            while True:
                if motion_supported:
                    state = await host.get_motion_state(channel)
                    if state is not None:
                        active = bool(state)
                        if previous_motion_state is False and active:
                            timestamp = time.time()
                            await asyncio.to_thread(
                                append_motion_marker,
                                camera_id,
                                recordings_root,
                                segment_seconds,
                                timestamp,
                                mount_point,
                            )
                            log.info(
                                "Motion marker recorded for camera %s",
                                camera_id,
                            )
                        previous_motion_state = active

                if people_supported:
                    ai_state = await host.get_ai_state(channel)
                    if ai_state is not None:
                        person_active = bool(ai_state.get("people", False))
                        if previous_people_state is False and person_active:
                            timestamp = time.time()
                            event_time = datetime.fromtimestamp(timestamp).astimezone()
                            message = (
                                f"Person detected: {camera_name} at "
                                f"{event_time:%Y-%m-%d %H:%M:%S}"
                            )
                            if alerts is not None:
                                alerts.notify(message)
                            log.info(
                                "Person detected for camera %s at %s",
                                camera_id,
                                event_time.isoformat(),
                            )
                        previous_people_state = person_active
                await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "Motion polling failed for camera %s (%s); retrying in 15s",
                camera_id,
                type(exc).__name__,
            )
        finally:
            try:
                await host.logout()
            except Exception:
                log.debug("Error closing motion client for camera %s", camera_id, exc_info=True)
        await asyncio.sleep(15)
