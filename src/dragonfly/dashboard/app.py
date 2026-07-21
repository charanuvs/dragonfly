"""Local-LAN dashboard (FastAPI).

Bound to the Pi's LAN interface only (config.dashboard.bind_host) — see
docs/network.md. Run directly for local dev:

    uvicorn dragonfly.dashboard.app:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Dragonfly")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/modules")
def list_modules() -> list[dict]:
    # TODO: wire to the hub's DeviceRegistry once the hub process and
    # dashboard are sharing state (in-process, or via the database).
    return []
