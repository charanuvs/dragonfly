# Architecture

## Overview

Dragonfly is a hub-and-spoke system. The Raspberry Pi is the hub: it is the
only device that maintains full state, the only device that writes to
storage, and the only device with any path to the internet. Everything else —
cameras, environmental sensors, door/window sensors, whatever gets added later
— is a spoke that only talks to the hub.

```
                    ┌─────────────────────────────┐
                    │        Raspberry Pi          │
                    │           (hub)               │
                    │                                │
   isolated  ───────┤  MQTT broker (Mosquitto)       │
   sensor    ◄──────┤  hub service (device registry, │
   network          │   recorder, event bus)          ├──────►  home LAN
   (no internet)     │  dashboard (FastAPI, LAN-only) │        (internet,
                    │  external HDD (recordings, DB)  │         your Mac, phone)
                    └─────────────────────────────┘
```

## Components

### Hub service (`src/dragonfly/hub/`)

The always-running core process on the Pi.

- **`config.py`** — loads `config/dragonfly.yaml` (per-deployment, not committed
  to git; see `config/dragonfly.example.yaml` for the template).
- **`registry.py`** — tracks every known module (camera, sensor): its id,
  type, last-seen time, and current status. This is the single source of
  truth for "what devices exist and are they alive."
- **`eventbus.py`** — thin wrapper around an MQTT broker running on the Pi
  itself (Mosquitto). Modules publish readings/events to topics like
  `dragonfly/<module_id>/state`; the hub subscribes to all of them. MQTT is
  used because it works over the isolated network with no internet
  dependency, is trivial to implement on constrained devices (ESP32, Pi Zero,
  etc.), and decouples modules from the hub's internal code.
- **`main.py`** — process entry point (`dragonfly-hub` console script),
  wires config → event bus → registry → recorder → dashboard, run under
  systemd (see `deploy/dragonfly-hub.service`).

### Modules (`src/dragonfly/modules/`)

Everything that isn't the hub. All modules implement the same interface
(`modules/base.py: SensorModule`) so the hub doesn't need special-case code
per device type: `module_id`, `module_type`, `start()`, `stop()`, and a
callback that publishes readings onto the event bus.

- **`modules/camera/`** — camera modules. The first one, **`rtsp_camera.py`**,
  covers the Outdoor West (`OW`) camera: an RTSP/ONVIF network camera, so the
  hub can't wire it directly like a Pi Camera Module. It's monitored with a
  lightweight heartbeat — a background thread does a plain TCP connect to the
  camera's RTSP port on an interval (`poll_interval_s` in config) and
  publishes `{online, type, ts}` to `dragonfly/<id>/heartbeat`. This is
  intentionally the smallest useful slice ("is OW reachable right now?")
  before building actual stream pulling/recording (`recorder.py`, still a
  stub) on top of it.
- Future sensor types are added as new subpackages under `modules/`, each a
  small driver that reads hardware and calls `publish()`.

Physical topology for modules is flexible: a sensor can be a peripheral wired
directly to the hub Pi (e.g. a Pi Camera Module), or a separate small device
(ESP32, Pi Zero W) on the isolated network that speaks MQTT to the hub. Either
way it looks the same to the hub service.

### Dashboard (`src/dragonfly/dashboard/`)

A FastAPI app serving a status page (heartbeat/online-offline per module
today; live views — camera snapshots/streams, sensor readings, recording
history — as those modules are built out). Binds only to the Pi's
LAN-reachable interface — never to the isolated sensor interface, never to a
tailscale/public interface without deliberately deciding to do so later.

`hub/main.py` runs the dashboard in the same process as the hub service
(uvicorn, alongside the MQTT event loop in a background thread) so both share
one in-memory `DeviceRegistry` with no extra IPC — the dashboard's
`/api/modules` endpoint just reads `app.state.registry` directly. This is
the simplest thing that works for a single-hub deployment; if the dashboard
ever needs to run as a separate process, that state moves to the SQLite
database described below instead.

### Storage

The external HDD is mounted on the Pi and holds:

- Recorded video (`recordings/<module_id>/<date>/...`)
- A local database (SQLite to start) for sensor readings/events and the
  device registry's persistent state

## Data flow

1. A module reads its hardware and publishes a message to the MQTT broker.
2. The hub's event bus receives it, updates the registry, and hands it to
   whichever subsystem cares (recorder for camera frames, a time-series
   logger for sensor readings).
3. The dashboard reads current state from the registry/database and renders
   it; it does not talk to modules directly.

## Why this shape

- Adding a new sensor type never requires touching the hub's core code —
  just a new module implementing the same small interface.
- The hub is the only thing that needs to be reliable and reachable; modules
  can be as cheap/flaky as necessary.
- MQTT plus a local broker means the whole thing works with zero internet
  dependency, which matches the network isolation goal in
  [`network.md`](network.md).
