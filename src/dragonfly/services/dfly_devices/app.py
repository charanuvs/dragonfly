from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from kasa.exceptions import KasaException

from dragonfly.common.config import load_config
from dragonfly.services.dfly_devices.kasa_switch import KasaSwitchController, run_auto_off_schedule
from dragonfly.services.dfly_devices.reolink_camera import ReolinkIrController

log = logging.getLogger("dragonfly.devices")


@asynccontextmanager
async def lifespan(application: FastAPI):
    controller = application.state.kasa_switch_controller
    switch_config = application.state.kasa_switch_config
    schedule_task = None
    button = None

    if controller is not None and switch_config is not None:
        schedule_task = asyncio.create_task(
            run_auto_off_schedule(controller, switch_config.timezone)
        )
        try:
            from gpiozero import Button

            button = Button(switch_config.gpio_pin, pull_up=True, bounce_time=0.05)
            loop = asyncio.get_running_loop()

            def toggle_from_button() -> None:
                future = asyncio.run_coroutine_threadsafe(controller.toggle(), loop)

                def report_result(result) -> None:
                    try:
                        state = result.result()
                    except KasaException as exc:
                        log.error("GPIO button failed to toggle %s: %s", controller.name, exc)
                    else:
                        log.info(
                            "GPIO button set %s to %s",
                            controller.name,
                            "on" if state else "off",
                        )

                future.add_done_callback(report_result)

            button.when_pressed = toggle_from_button
        except Exception:
            log.exception("GPIO button unavailable; device API control remains enabled")

    try:
        yield
    finally:
        if button is not None:
            button.close()
        if schedule_task is not None:
            schedule_task.cancel()
            try:
                await schedule_task
            except asyncio.CancelledError:
                pass
        for controller in application.state.camera_ir_controllers.values():
            try:
                await controller.close()
            except Exception:
                log.exception("Could not close camera IR controller")


app = FastAPI(title="Dragonfly Devices", lifespan=lifespan)
app.state.kasa_switch_config = None
app.state.kasa_switch_controller = None
app.state.camera_ir_controllers = {}
app.state.camera_config_loader = load_config


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/switch")
async def switch_status(request: Request) -> dict:
    controller = request.app.state.kasa_switch_controller
    if controller is None:
        raise HTTPException(status_code=404, detail="Switch not configured")
    try:
        switch_config = request.app.state.kasa_switch_config
        return {
            "name": controller.name,
            "on": await controller.get_state(),
            "host": controller.host,
            "timezone": switch_config.timezone if switch_config else "America/Los_Angeles",
        }
    except Exception as exc:
        log.warning("Could not read Kasa switch %s: %s", controller.name, exc)
        raise HTTPException(status_code=503, detail="Switch unavailable") from exc


@app.post("/switch/toggle")
async def toggle_switch(request: Request) -> dict:
    controller = request.app.state.kasa_switch_controller
    if controller is None:
        raise HTTPException(status_code=404, detail="Switch not configured")
    try:
        switch_config = request.app.state.kasa_switch_config
        return {
            "name": controller.name,
            "on": await controller.toggle(),
            "host": controller.host,
            "timezone": switch_config.timezone if switch_config else "America/Los_Angeles",
        }
    except Exception as exc:
        log.warning("Could not toggle Kasa switch %s: %s", controller.name, exc)
        raise HTTPException(status_code=503, detail="Switch unavailable") from exc


def _camera_ir_controller(request: Request, camera_id: str) -> ReolinkIrController | None:
    config = request.app.state.camera_config_loader()
    camera = next(
        (camera for camera in config.cameras if camera.id == camera_id and camera.ir_control),
        None,
    )
    if camera is None:
        return None
    controllers = request.app.state.camera_ir_controllers
    if camera_id not in controllers:
        controllers[camera_id] = ReolinkIrController(camera.rtsp_url)
    return controllers[camera_id]


@app.get("/cameras/{camera_id}/ir")
async def camera_ir_status(camera_id: str, request: Request) -> dict:
    controller = _camera_ir_controller(request, camera_id)
    if controller is None:
        raise HTTPException(status_code=404, detail="IR control not configured")
    try:
        return await controller.get_state()
    except Exception as exc:
        log.warning("Could not read IR mode for camera %s: %s", camera_id, exc)
        raise HTTPException(status_code=503, detail="Camera IR status unavailable") from exc


@app.post("/cameras/{camera_id}/ir/cycle")
async def cycle_camera_ir(camera_id: str, request: Request) -> dict:
    controller = _camera_ir_controller(request, camera_id)
    if controller is None:
        raise HTTPException(status_code=404, detail="IR control not configured")
    try:
        return await controller.cycle_mode()
    except Exception as exc:
        log.warning("Could not change IR mode for camera %s: %s", camera_id, exc)
        raise HTTPException(status_code=503, detail="Camera IR control unavailable") from exc