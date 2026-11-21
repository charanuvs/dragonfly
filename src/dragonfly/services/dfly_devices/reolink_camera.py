"""Local control for supported Reolink camera features."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote, urlsplit

from reolink_aio.api import Host

IR_MODES = ("Auto", "Off")


class ReolinkIrController:
    def __init__(self, rtsp_url: str, channel: int = 0) -> None:
        parts = urlsplit(rtsp_url)
        if parts.hostname is None or parts.username is None or parts.password is None:
            raise ValueError("IR control requires camera credentials in rtsp_url")
        self._host = Host(
            parts.hostname,
            unquote(parts.username),
            unquote(parts.password),
            port=80,
            use_https=False,
            protocol="rtsp",
        )
        self._channel = channel
        self._ready = False
        self._lock = asyncio.Lock()

    async def _read_mode(self) -> str:
        if not self._ready:
            await self._host.get_host_data()
            if not self._host.supported(self._channel, "ir_lights"):
                raise RuntimeError("Camera does not support IR light control")
            self._ready = True

        await self._host.get_state("GetIrLights", ch=self._channel)
        mode = self._host._ir_settings.get(self._channel, {}).get("state")
        if mode not in IR_MODES:
            raise RuntimeError("Camera returned an unsupported IR mode")
        return mode

    @staticmethod
    def _response(mode: str) -> dict:
        index = IR_MODES.index(mode)
        return {
            "mode": mode,
            "available_modes": list(IR_MODES),
            "next_mode": IR_MODES[(index + 1) % len(IR_MODES)],
        }

    async def get_state(self) -> dict:
        async with self._lock:
            return self._response(await self._read_mode())

    async def cycle_mode(self) -> dict:
        async with self._lock:
            mode = await self._read_mode()
            next_mode = IR_MODES[(IR_MODES.index(mode) + 1) % len(IR_MODES)]
            await self._host.set_ir_lights(self._channel, enable=next_mode == "Auto")
            return self._response(await self._read_mode())

    async def close(self) -> None:
        await self._host.logout()