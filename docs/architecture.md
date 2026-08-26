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
   (no internet)     │  │  (recorder,   │ MQTT │  (dashboard +   ││──►  home LAN
                    │  │  live ffmpeg) │      │   watchdog)     ││     (internet,
                    │  │               │◄─────┤  watches capture ││      your Mac, phone)
                    │  └──────────────┘ check └────────────────┘│
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

Does the work of talking to cameras: the recorders and the live-view ffmpeg
manager (below). Publishes recording events to MQTT; has no HTTP server and
no direct knowledge of the dashboard. Run under systemd as
`dragonfly-capture.service`.

Capture deliberately monitors **nothing**, including itself — all health
checking belongs to the watchdog (below), which runs in the portal process.
A component reporting its own health can't report having died: capture's
earlier self-heartbeat simply went silent when capture crashed, leaving the
dashboard to infer the problem from staleness. Checking from outside is both
simpler and more truthful.

### Watchdog (`src/dragonfly/watchdog/`)

Runs as a background thread in the portal process. Four checks, all
external observations rather than self-reports:

1. **Camera reachable** — TCP connect to the RTSP port.
2. **Storage healthy** — mounted, readable (`os.statvfs`, which fails with
   EIO on a mount whose drive was yanked, the case where `os.path.ismount()`
   still says True), and not full.
3. **Recording actually happening** — verified by finding the newest `.mp4`
   on disk and checking it's been written to recently. Notably this does not
   trust a "segment started" event; the recorder saying it started writing
   isn't proof bytes landed.
4. **Capture process up** — `systemctl is-active dragonfly-capture`, which
   stays correct even if capture is hard-killed or never started.

Results go into portal's `DeviceRegistry` (so the dashboard renders them
like any other module, including two pseudo-modules `_capture` and
`_storage`) and are published retained over MQTT for anything else that
cares. Intervals and thresholds are configurable under `watchdog:` in
`config/dragonfly.yaml`.

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

- **`modules/camera/`** — camera modules. The Outdoor West (`OW`) camera is
  RTSP/ONVIF, so the hub can't wire it directly like a Pi Camera Module.
  Recording is an opt-in (`record: true`) module, **`recorder.py`**: an
  ffmpeg-based segmented recorder with gapless cutover and storage-watermark
  cleanup — see [`recording.md`](recording.md) for the full design and the
  storage math behind its defaults. **`rtsp_camera.py`** holds the
  reachability check (`parse_rtsp_target`, a plain TCP connect to the RTSP
  port); the watchdog is what calls it now, on `watchdog.interval_s`.
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
event loop and the watchdog thread) in a process separate from capture. The
dashboard's `/api/modules` endpoint reads `app.state.registry`, but that
registry is portal's own — populated by the watchdog's checks plus capture's
MQTT events — not a shared object with capture. There is no direct IPC
between the two processes at all, only MQTT plus the shared live-view
directory described next.

Each module's card also renders a free-form `details` dict — whatever the
check that produced it found worth reporting (disk usage, systemd state,
newest segment and its age, last error). That keeps the UI generic: a new
check can surface useful facts without the dashboard needing to know
anything about it.

Note the division of labour on recording status: the watchdog owns
`recording_active`, judged from files on disk, while capture's own MQTT
events contribute only the *reason* something failed (`not_mounted`,
`write_failed`, `segment_failed`), shown as "capture says". Capture
announcing that it started a segment is not evidence that bytes were
written — keeping the claim and the verification separate is the point.

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
