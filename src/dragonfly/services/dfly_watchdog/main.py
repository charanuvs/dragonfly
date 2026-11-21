from __future__ import annotations

import logging
import signal
import threading

from dragonfly.common.alerts import AlertManager, Ntfy
from dragonfly.common.config import load_config
from dragonfly.common.eventbus import EventBus
from dragonfly.common.registry import DeviceRegistry
from dragonfly.services.dfly_watchdog.monitor import Watchdog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dragonfly.dfly_watchdog")


def run() -> None:
    config = load_config()
    bus = EventBus(host=config.mqtt.host, port=config.mqtt.port)
    bus.connect()
    bus.loop_start()

    try:
        sender = Ntfy.from_environment()
    except ValueError:
        log.error("ntfy health alerts disabled: invalid configuration")
        sender = None
    alerts = AlertManager(sender) if sender is not None else None
    if alerts is not None:
        alerts.start()

    watchdog = Watchdog(config, DeviceRegistry(), bus, alerts)
    watchdog.start()
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())
    log.info("dfly-watchdog started; checking every %.0fs", config.watchdog.interval_s)
    try:
        shutdown.wait()
    finally:
        watchdog.stop()
        if alerts is not None:
            alerts.stop()
        bus.loop_stop()


if __name__ == "__main__":
    run()