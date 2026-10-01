# Dragonfly

Dragonfly is a home automation, security, and monitoring system built around a
dedicated hub host. The hub controls and aggregates data from cameras and other
sensors, records footage to attached storage, and serves a dashboard on the
local network.

## Hardware & Architecture

- **Hub Host** — central hub, always on (mini-PC, home server, or single-board computer)
- **External Storage** — attached drive (HDD/SSD/USB), stores recordings and time-series data
- **Isolated Network** — router/AP hosting the isolated sensor/IoT network
- **Internet Uplink** — WAN connection (uplink only for the hub; sensors remain offline)
- Cameras and sensors added over time

## Design principles

1. **Single hub.** The hub host is the only device that talks to every
   sensor and the only device with a full view of the system. Sensor modules
   are as dumb as possible; the hub owns state, recording, and orchestration.
2. **Network isolation.** Sensors and cameras live on a network segment that
   has no route to the internet. Only the hub is dual-homed: one interface on
   that isolated segment, one interface with internet access.
3. **LAN-only dashboard with optional mesh access.** The web dashboard is reachable from your home
   network with no open router ports. For viewing live feeds or the dashboard securely while away from home, a private mesh network (such as Tailscale) can be used.
4. **Out-of-the-box installation.** Pre-packaged release installers configure systemd services, mount storage, and install dependencies cleanly.

See `docs/` for the full design:

- [`docs/architecture.md`](docs/architecture.md) — software architecture
- [`docs/network.md`](docs/network.md) — network topology and isolation setup
- [`docs/recording.md`](docs/recording.md) — video recording design + storage math

## Compatibility

Dragonfly is platform-agnostic and designed to run on any modern Linux system:

- **Supported Operating Systems**:
  - **Linux (Production Service)**: Any Debian- or Ubuntu-based distribution with `systemd` (Debian 12+, Ubuntu 22.04+, Raspberry Pi OS, DietPi, Armbian).
  - **macOS / Linux (Development)**: Full support for local testing, running pytest, and developing modules (`pip install -e ".[dev]"`).
- **Supported CPU Architectures**:
  - `x86_64` / `amd64` (Intel/AMD mini-PCs, NUCs, ThinkCentre Tiny, home servers, Proxmox VMs)
  - `aarch64` / `arm64` (Raspberry Pi 4 / 5, Odroid, Orange Pi, Apple Silicon for dev)
  - `armv7l` (32-bit ARM SBCs)
- **Recommended Hardware**:
  - Any low-power x86 mini-PC (e.g. Intel N100/N95, Celeron, Core i3/i5) or ARM SBC (Raspberry Pi 4/5 with 2GB+ RAM).
  - External USB 3.0 HDD or SSD formatted as `ext4` for video recordings and database.
  - Dedicated Ethernet interface or dual network interfaces (Ethernet + Wi-Fi) for network isolation.

## Project layout

```
dragonfly/
  src/dragonfly/
    hub/         # core service: config, device registry, event bus, main loop
    modules/      # sensor/camera drivers, all implementing a common interface
    dashboard/    # local web UI (FastAPI)
  config/         # example config, per-deployment config lives outside git
  deploy/         # systemd units, storage setup, and install scripts
  docs/           # architecture, network, and recording design docs
  tests/
```

## Installation

Dragonfly provides an out-of-the-box installer that sets up system dependencies, an isolated application environment (`/opt/dragonfly`), dedicated service user, USB storage formatting and mounting, and systemd services.

Run on the Linux host:

```bash
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | sudo bash
```

After installation:
- **Web Dashboard**: `http://<hub-ip>:8000` (or via your Tailscale IP/hostname)
- **Configuration**: `/etc/dragonfly/dragonfly.yaml`
- **Re-configure Storage**: `sudo dragonfly-setup-storage`
- **Update to Latest Release**: `sudo dragonfly-update`
- **Uninstall**: `sudo dragonfly-uninstall`

### Remote Access (Tailscale Mesh Network)

To view the web portal and camera feeds away from home without forwarding any ports on your router, install [Tailscale](https://tailscale.com) on your hub host and personal devices. You can access the dashboard securely from anywhere on your mesh network via `http://<tailscale-ip-or-magicdns>:8000`.

## Status

**OW** (Outdoor West, an RTSP/ONVIF camera) is monitored via a TCP-connect
heartbeat visible live on the dashboard (`http://<hub-lan-ip>:8000`), and
records continuously in gapless 5-minute segments to the external drive
(see [`docs/recording.md`](docs/recording.md) for the retention/storage
tradeoffs — an 8GB drive only holds a few days at usable quality). Other
sensor types are yet to come.

## Managing Cameras & Configuration Changes

Camera streams, polling intervals, and recording rules are defined in `/etc/dragonfly/dragonfly.yaml` (or `config/dragonfly.yaml` during local development).

Configuration is loaded once at service startup. When you add, edit, or remove cameras:
1. Edit `/etc/dragonfly/dragonfly.yaml`.
2. Restart the services to apply changes:
   ```bash
   # Restart both services
   sudo systemctl restart dragonfly-capture dragonfly-portal

   # Or restart selectively:
   sudo systemctl restart dragonfly-capture   # if RTSP URLs or recording settings changed
   sudo systemctl restart dragonfly-portal    # if display names or watchdog polling changed
   ```
Restarting takes 1–2 seconds. Live recording and watchdog checks resume immediately with the updated configuration.


## License

MIT — see [`LICENSE`](LICENSE).
