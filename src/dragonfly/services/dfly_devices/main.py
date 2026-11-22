from __future__ import annotations

import logging

import uvicorn

from dragonfly.common.config import load_config
from dragonfly.services.dfly_devices.app import app
from dragonfly.services.dfly_devices.kasa_switch import KasaSwitchController

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run() -> None:
    config = load_config()
    app.state.kasa_switch_config = config.kasa_switch
    app.state.kasa_switch_controller = (
        KasaSwitchController(
            config.kasa_switch.host,
            config.kasa_switch.name,
            config.kasa_switch.device_id,
        )
        if config.kasa_switch
        else None
    )
    uvicorn.run(app, host="127.0.0.1", port=8001)


if __name__ == "__main__":
    run()