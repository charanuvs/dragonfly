# Architecture

## Overview

Dragonfly is a hub-and-spoke system. The Raspberry Pi is the hub: it is the
only device that maintains full state, the only device that writes to
storage, and the only device with any path to the internet. Everything else —
cameras, environmental sensors, door/window sensors, whatever gets added later
— is a spoke that only talks to the hub.

On the Pi itself, the hub is split into **two independent processes** —
capture and portal — coordinating only over MQTT, not by sharing memory:

```
                    ┌───────────────────────────────────────┐
                    │              Raspberry Pi                │
                    │                                           │
   isolated  ───────┤  MQTT broker (Mosquitto)                  │
   sensor    ◄──────┤  ┌──────────────┐      ┌────────────────┐│
   network          │  │   capture     │◄────►│     portal      ││
   (no internet)     │  │ (heartbeat,   │ MQTT │  (FastAPI       ││──►  home LAN
                    │  │  recorder,    │      │   dashboard,    ││     (internet,
                    │  │  live ffmpeg) │      │   own registry) ││      your Mac, phone)
                    │  └──────────────┘      └────────────────┘│
                    │  external HDD (recordings)                │
                    └───────────────────────────────────────┘
```

Why split it this way: early on, both lived in one process, and every
dashboard tweak meant a `systemctl restart` that also killed whatever
recording was mid-flight. Splitting them means restarting the dashboard
after a UI change never interrupts a recording or a live stream, and vice
versa — the two genuinely have different release cadences (the dashboard
changes constantly during development; capture should barely ever need to
restart once it's working).

## Components

### Shared code (`src/dragonfly/hub/`)

Used by both processes, contains no process-specific logic itself.

- **`config.py`** — loads `config/dragonfly.yaml` (per-deployment, not committed
  to git; see `config/dragonfly.example.yaml` for the template).
- **`registry.py`** — tracks every known module (camera, sensor): its id,
  type, last-seen time, and current status. Each process that needs a view
  of "what devices exist and are they alive" builds its own instance from
  the MQTT stream — there's no single shared registry object anymore, since
  there's no single process to own it.
- **`eventbus.py`** — thin wrapper around an MQTT broker running on the Pi
  itself (Mosquitto). Modules publish readings/events to topics like
  `dragonfly/<module_id>/state`; anyone who cares subscribes. MQTT is used
  because it works over the isolated network with no internet dependency,
  is trivial to implement on constrained devices (ESP32, Pi Zero, etc.), and
  — critically for the two-process split — decouples publishers from
  subscribers entirely, which is exactly what capture/portal needed.
- **`sysinfo.py`** — CPU/mem/disk stats, used only by portal (self-contained,
  no capture dependency).

### Capture process (`hub/capture_main.py`, console script `dragonfly-capture`)

Owns everything that actually talks to a camera: the heartbeat modules, the
recorders, and the live-view ffmpeg manager (below). Publishes
heartbeat/recording events to MQTT; has no HTTP server and no direct
knowledge of the dashboard. Run under systemd as `dragonfly-capture.service`.

Capture also publishes its own process-level heartbeat every
`CAPTURE_HEALTH_INTERVAL_S` (15s), under a pseudo module-id (`_capture`, not
a real camera). This reuses the existing heartbeat plumbing/UI, so if
capture itself dies entirely — not just a single camera going unreachable —
that shows up on the dashboard as its own card going stale/offline, rather
than only being inferable from every camera's status going stale at once.

### Portal process (`hub/portal_main.py`, console script `dragonfly-portal`)

Serves the web dashboard (FastAPI/uvicorn). Has no direct reference to any
capture-side object — it builds its own `DeviceRegistry` by independently
subscribing to the same `dragonfly/#` MQTT topics capture publishes to, and
for live view, publishes a request over MQTT and then reads the resulting
files straight off disk from a well-known shared directory (rather than
holding any reference to capture's `LiveStreamManager`). Run under systemd
as `dragonfly-portal.service`.

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
  intentionally the smallest useful slice ("is OW reachable right now?").
  Actual recording is a separate, opt-in (`record: true`) module,
  **`recorder.py`**: an ffmpeg-based segmented recorder with gapless cutover
  and date-based retention cleanup — see [`recording.md`](recording.md) for
  the full design and the storage math behind its defaults.
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

`hub/portal_main.py` runs the dashboard (uvicorn, alongside its own MQTT
event loop in a background thread) in a process separate from capture. The
dashboard's `/api/modules` endpoint reads `app.state.registry`, but that
registry is portal's own — built by independently subscribing to MQTT and
replaying heartbeat/recording events — not a shared object with capture.
There is no direct IPC between the two processes at all, only MQTT plus the
shared live-view directory described next.

**Live view** (`dashboard/live.py`, `/live/<camera-id>`) is a third, separate
way capture touches a camera besides the heartbeat and the recorder: an
on-demand `ffmpeg` process remuxes (not re-encodes — `-c:v copy`, cheap on
CPU) the camera's main/HD stream into short HLS segments. Since capture owns
this process and portal serves the HTTP request, the two coordinate over
MQTT: portal publishes a `live_start_request` event on each segment/playlist
fetch (which doubles as the "still watching" keep-alive), capture's
`LiveStreamManager` starts/keeps the ffmpeg process running and writes HLS
segments to a well-known shared directory (`/tmp/dragonfly-live/<camera-id>`
by default), and portal reads the resulting files straight off disk to serve
to the browser via `hls.js`. It auto-stops after ~60s with no requests,
because unlike the always-on heartbeat/recorder, a third concurrent
connection to the camera is the one most likely to bump into a cheap
camera's connection limit — see the caveat in `docs/recording.md`/README if
live view and a recording cutover overlap and one fails.

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
