"""Entry point for the dfly-capture service."""
from __future__ import annotations

import logging
import signal
import threading

from dragonfly.common.config import load_config
from dragonfly.common.eventbus import EventBus
from dragonfly.services.dfly_capture.live import LiveStreamManager
from dragonfly.services.dfly_capture.recorder import SegmentedRecorder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.capture")


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
            mount_point=config.storage.mount_point,
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

    log.info("dfly-capture starting up (%d recorder(s))", len(recorders))

    # systemd sends SIGTERM on stop/restart; Python only raises KeyboardInterrupt
    # for SIGINT, so without this a `systemctl restart` would skip our cleanup
    # (closing ffmpeg log file handles, stopping live-view processes cleanly)
    # and rely entirely on systemd's blunter cgroup-kill instead.
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())

    try:
        shutdown.wait()
    finally:
        for rec in recorders:
            rec.stop()
        live_manager.stop_all()
        bus.loop_stop()


if __name__ == "__main__":
    run()
