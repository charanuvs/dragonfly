from __future__ import annotations

import asyncio
import logging
import signal

from dragonfly.common.alerts import AlertManager, Ntfy
from dragonfly.common.config import load_config
from dragonfly.services.dfly_detect.motion import monitor_motion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.detection")


async def _run() -> None:
    config = load_config()
    try:
        sender = Ntfy.from_environment("DRAGONFLY_NTFY_PERSON")
    except ValueError:
        log.error("person notifications disabled: invalid configuration")
        sender = None
    alerts = AlertManager(sender) if sender is not None else None
    if alerts is not None:
        alerts.start()

    tasks = [
        asyncio.create_task(
            monitor_motion(
                camera.id,
                camera.name,
                camera.rtsp_url,
                config.storage.recordings_path,
                camera.segment_seconds,
                config.storage.mount_point,
                alerts,
            )
        )
        for camera in config.cameras
        if camera.motion_events
    ]
    if not tasks:
        log.info("no cameras have detection enabled; waiting for shutdown")

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, shutdown.set)
    log.info("dfly-detect started with %d camera monitor(s)", len(tasks))
    try:
        await shutdown.wait()
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if alerts is not None:
            alerts.stop()


def run() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    run()