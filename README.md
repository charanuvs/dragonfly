# Dragonfly

Dragonfly is a home automation, security, and monitoring system built around a
Raspberry Pi hub. The hub controls and aggregates data from cameras and other
sensors, records footage to attached storage, and serves a dashboard on the
local network.

## Hardware (current)

- Raspberry Pi — central hub, always on
- External HDD — attached to the Pi, stores recordings and time-series data
- TP-Link Deco mesh system — home Wi-Fi, hosts the isolated sensor/IoT network
- TP-Link Archer modem — internet uplink (WAN)
- More sensors/cameras to be added over time

## Design principles

1. **Single hub.** The Raspberry Pi is the only device that talks to every
   sensor and the only device with a full view of the system. Sensor modules
   are as dumb as possible; the hub owns state, recording, and orchestration.
2. **Network isolation.** Sensors and cameras live on a network segment that
   has no route to the internet. Only the hub is dual-homed: one interface on
   that isolated segment, one interface with internet access.
3. **LAN-only dashboard.** The web dashboard is reachable from your home
   network but is never exposed to the internet.
4. **Develop on the Mac, deploy to the Pi.** Code is written and tested on a
   Mac, pushed to GitHub, and deployed to the Pi remotely over a private
   Tailscale VPN — no inbound ports opened on the home network.

See `docs/` for the full design:

- [`docs/architecture.md`](docs/architecture.md) — software architecture
- [`docs/network.md`](docs/network.md) — network topology and isolation setup
- [`docs/deployment.md`](docs/deployment.md) — Tailscale + remote deploy workflow

## Project layout

```
dragonfly/
  src/dragonfly/
    hub/         # core service: config, device registry, event bus, main loop
    modules/      # sensor/camera drivers, all implementing a common interface
    dashboard/    # local web UI (FastAPI)
  config/         # example config, per-deployment config lives outside git
  deploy/         # systemd unit, install script, deploy script, Tailscale notes
  docs/           # architecture, network, and deployment design docs
  tests/
```

## Status

Early scaffold, with one module working end-to-end: **OW** (Outdoor West,
an RTSP/ONVIF camera) is monitored via a TCP-connect heartbeat, and the
dashboard shows it live at `http://<pi-lan-ip>:8000`. Actual video
capture/recording is still a stub. Other sensor types are yet to come.

## Setup (Mac, for development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## License

MIT — see [`LICENSE`](LICENSE).
