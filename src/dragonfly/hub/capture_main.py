"""Entry point for the capture process (console script: `dragonfly-capture`).

Owns everything that actually talks to a camera: the heartbeat, the
recorder, and the live-stream ffmpeg manager. Deliberately has no HTTP
server of its own — the portal process (portal_main.py) serves the web
UI, reading state that capture publishes over MQTT (heartbeat/recording
events) and, for live view, files capture writes to a well-known shared
directory. See docs/architecture.md for why these are split into two
processes: so restarting the dashboard (frequent, e.g. every UI tweak)
never interrupts an in-progress recording, and vice versa.
"""
from __future__ import annotations

import json
import logging
import signal
import threading
import time

from dragonfly.dashboard.live import LiveStreamManager
from dragonfly.hub.config import load_config
from dragonfly.hub.eventbus import EventBus
from dragonfly.modules.camera.recorder import SegmentedRecorder
from dragonfly.modules.camera.rtsp_camera import RtspCameraModule

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.capture")

# Pseudo module-id (not a real camera) capture uses to report its own
# liveness, reusing the existing heartbeat mechanism/UI — so a fully-dead
# capture process shows up on the dashboard as its own card, instead of only
# being inferable from every camera's heartbeat/recording status going
# stale at once. Portal applies the same staleness check to this as any
# other heartbeat (see dashboard/app.py).
CAPTURE_HEALTH_MODULE_ID = "_capture"
CAPTURE_HEALTH_INTERVAL_S = 15


def run() -> None:
    config = load_config()
    bus = EventBus(host=config.mqtt.host, port=config.mqtt.port)

    live_manager = LiveStreamManager({cam.id: cam.rtsp_url for cam in config.cameras})

    def on_event(topic: str, _payload: bytes) -> None:
        # dragonfly/<module_id>/<subtopic> — the only inbound command capture
        # currently listens for is the portal asking it to (ensure it) starts
        # live view for a camera someone's looking at.
        parts = topic.split("/")
        if len(parts) < 3:
            return
        module_id, subtopic = parts[1], parts[2]
        if subtopic == "live_start_request" and live_manager.has(module_id):
            live_manager.ensure_running(module_id)

    bus.on_event(on_event)
    bus.connect()
    bus.loop_start()

    camera_modules = [
        RtspCameraModule(
            module_id=cam.id,
            bus=bus,
            rtsp_url=cam.rtsp_url,
            poll_interval_s=cam.poll_interval_s,
            timeout_s=cam.timeout_s,
        )
        for cam in config.cameras
    ]
    for mod in camera_modules:
        try:
            mod.start()
        except Exception:
            log.exception("failed to start heartbeat module %s — continuing without it", mod.module_id)

    recorders = [
        SegmentedRecorder(
            module_id=cam.id,
            bus=bus,
            rtsp_url=cam.recording_rtsp_url or cam.rtsp_url,
            recordings_root=config.storage.recordings_path,
            fps=cam.fps,
            bitrate_kbps=cam.bitrate_kbps,
            segment_seconds=cam.segment_seconds,
            overlap_seconds=cam.overlap_seconds,
            high_watermark_pct=cam.high_watermark_pct,
            low_watermark_pct=cam.low_watermark_pct,
            retention_days=cam.retention_days,
        )
        for cam in config.cameras
        if cam.record
    ]
    started_recorders = []
    for rec in recorders:
        try:
            rec.start()
            started_recorders.append(rec)
        except Exception:
            log.exception("failed to start recorder %s — continuing without it", rec.module_id)
    recorders = started_recorders

    live_manager.start_reaper()

    log.info(
        "dragonfly-capture starting up (%d camera module(s), %d recorder(s))",
        len(camera_modules),
        len(recorders),
    )

    # systemd sends SIGTERM on stop/restart; Python only raises KeyboardInterrupt
    # for SIGINT, so without this a `systemctl restart` would skip our cleanup
    # (closing ffmpeg log file handles, stopping live-view processes cleanly)
    # and rely entirely on systemd's blunter cgroup-kill instead.
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    def _health_loop() -> None:
        start_ts = time.time()
        while not shutdown.is_set():
            bus.publish(
                CAPTURE_HEALTH_MODULE_ID,
                "heartbeat",
                json.dumps(
                    {
                        "type": "capture_process",
                        "online": True,
                        "ts": time.time(),
                        "uptime_s": round(time.time() - start_ts, 1),
                        "cameras": len(camera_modules),
                        "recorders": len(recorders),
                    }
                ),
            )
            shutdown.wait(CAPTURE_HEALTH_INTERVAL_S)

    threading.Thread(target=_health_loop, daemon=True, name="capture-health").start()

    try:
        shutdown.wait()
    finally:
        for mod in camera_modules:
            mod.stop()
        for rec in recorders:
            rec.stop()
        live_manager.stop_all()
        # Explicit "going offline" so the dashboard reflects a clean shutdown
        # immediately rather than waiting ~3x the health interval to go stale.
        bus.publish(
            CAPTURE_HEALTH_MODULE_ID,
            "heartbeat",
            json.dumps({"type": "capture_process", "online": False, "ts": time.time()}),
        )
        bus.loop_stop()


if __name__ == "__main__":
    run()
