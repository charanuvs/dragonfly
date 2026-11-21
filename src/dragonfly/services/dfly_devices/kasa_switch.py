"""Local control and scheduled shutoff for a Kasa HS210 switch."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from kasa import (
    Credentials,
    Device,
    DeviceConfig,
    DeviceConnectionParameters,
    DeviceEncryptionType,
    DeviceFamily,
)

log = logging.getLogger(__name__)


class KasaSwitchController:
    def __init__(self, host: str, name: str) -> None:
        self.host = host
        self.name = name
        self._lock = asyncio.Lock()
        self._connection_type = DeviceConnectionParameters(
            device_family=DeviceFamily.IotSmartPlugSwitch,
            encryption_type=DeviceEncryptionType.Klap,
            login_version=2,
        )

    async def _apply(self, target: bool | None, *, toggle: bool = False) -> bool:
        async with self._lock:
            device = await Device.connect(
                config=DeviceConfig(
                    host=self.host,
                    credentials=Credentials(),
                    connection_type=self._connection_type,
                )
            )
            try:
                await device.update()
                desired = not device.is_on if toggle else target
                if desired is not None and device.is_on != desired:
                    await device.set_state(desired)
                    await device.update()
                return bool(device.is_on)
            finally:
                await device.disconnect()

    async def get_state(self) -> bool:
        return await self._apply(None)

    async def toggle(self) -> bool:
        return await self._apply(None, toggle=True)

    async def turn_off(self) -> bool:
        return await self._apply(False)


def next_auto_off(now: datetime, timezone_name: str) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone)
    candidates = []
    for day_offset in (0, 1):
        target_date = local_now.date() + timedelta(days=day_offset)
        candidates.extend(
            datetime.combine(target_date, time(hour=hour), tzinfo=zone)
            for hour in (0, 12)
        )
    return min(target for target in candidates if target > local_now)


async def run_auto_off_schedule(controller: KasaSwitchController, timezone_name: str) -> None:
    zone = ZoneInfo(timezone_name)
    while True:
        now = datetime.now(zone)
        target = next_auto_off(now, timezone_name)
        await asyncio.sleep((target - now).total_seconds())
        while True:
            try:
                await controller.turn_off()
                log.info("Scheduled shutoff completed for %s", controller.name)
                break
            except Exception:
                log.exception("Scheduled shutoff failed for %s; retrying in 60s", controller.name)
                await asyncio.sleep(60)