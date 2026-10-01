# Dragonfly

Tired of paying monthly cloud subscriptions just to record your own security cameras? Worried about third parties accessing your private camera feeds?

Dragonfly is a lightweight, local-first home security and camera hub. Keep everything on your own network, store continuous recordings on your own drives, and access your cameras without paying subscriptions or exposing ports.

- **100% Local**: Camera feeds and sensor data never leave your home network.
- **Own Your Storage**: Continuous, gapless recording with automatic retention management.
- **Zero Cloud Subscriptions**: Complete control over your hardware and footage.

---

## Quick Install

Run this command on your Linux host (Ubuntu, Debian, Raspberry Pi OS, etc.) or macOS (Apple Silicon / Intel):

```bash
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | bash
```
*(On Linux, run with `sudo bash`).*

The installer takes care of everything:
- Installs prerequisites (`ffmpeg`, `mosquitto`, Python) via `apt` (Linux) or Homebrew (macOS)
- Interactively configures external USB storage or local disk storage
- Configures and starts background services (`systemd` on Linux, `launchd` on macOS)

Once installed:
- **Web Dashboard**: `http://localhost:8000` (or `http://<hub-ip>:8000`)
- **Config**: `/etc/dragonfly/dragonfly.yaml` (Linux) or `~/.config/dragonfly/dragonfly.yaml` (macOS)
- **Restart**: `dragonfly-restart`
- **Update**: `dragonfly-update`
- **Uninstall**: `dragonfly-uninstall` (or `curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/uninstall.sh | bash`)

---

## Releases & Tarball Install

To install from an official release archive rather than curling the main installer script:

```bash
# 1. Download & unpack the latest release tarball
curl -fsSL -O https://github.com/charanuvs/dragonfly/releases/latest/download/dragonfly.tar.gz
tar -xzf dragonfly.tar.gz
cd dragonfly

# 2. Run the installer
bash deploy/install.sh         # macOS
sudo bash deploy/install.sh    # Linux
```

---

## Adding Cameras

Edit your configuration (`/etc/dragonfly/dragonfly.yaml` on Linux or `~/.config/dragonfly/dragonfly.yaml` on macOS) and add your camera's RTSP stream:

```yaml
cameras:
  - id: front_door
    name: "Front Door"
    rtsp_url: "rtsp://<user>:<password>@<camera-ip>:554/stream1"
    record: true
    fps: 10
    bitrate_kbps: 200
    segment_seconds: 300
```

Apply changes by restarting the services:
```bash
dragonfly-restart
```

---

## Remote Access (Optional)

To view your cameras away from home without opening any router ports, install [Tailscale](https://tailscale.com) on your hub host. You can securely access the dashboard from your phone or laptop at `http://<tailscale-ip>:8000`.

---

## Compatibility

- **Operating Systems**:
  - **Linux**: Debian 12+, Ubuntu 22.04+, Raspberry Pi OS, DietPi, or any distro with `systemd`.
  - **macOS**: macOS 12+ Monterey, Ventura, Sonoma, Sequoia (with [Homebrew](https://brew.sh)).
- **Architectures**: `x86_64` (Intel/AMD), `arm64` (Apple Silicon M1/M2/M3/M4, Raspberry Pi 4/5).
- **Storage**: External USB 3.0 HDD/SSD (ext4 on Linux, APFS/ExFAT on macOS) or internal storage.

---

## Development

```bash
git clone https://github.com/charanuvs/dragonfly.git
cd dragonfly
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

For design details, see [`docs/architecture.md`](docs/architecture.md), [`docs/network.md`](docs/network.md), and [`docs/recording.md`](docs/recording.md).

## License

MIT — see [`LICENSE`](LICENSE).
