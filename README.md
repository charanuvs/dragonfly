# Dragonfly

Tired of paying monthly cloud subscriptions just to record your own security cameras? Worried about third parties accessing your private camera feeds?

Dragonfly is a lightweight, local-first home security and camera hub. Keep everything on your own network, store continuous recordings on your own drives, and access your cameras without paying subscriptions or exposing ports.

- **100% Local**: Camera feeds and sensor data never leave your home network.
- **Own Your Storage**: Continuous, gapless recording with automatic retention management.
- **Zero Cloud Subscriptions**: Complete control over your hardware and footage.

---

## Quick Install

Run this command on your Linux hub host (Ubuntu, Debian, Raspberry Pi OS, etc.):

```bash
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | sudo bash
```

The installer takes care of everything:
- Installs prerequisites (`ffmpeg`, `mosquitto`, Python)
- Interactively configures and mounts your external USB storage
- Configures and starts systemd background services

Once installed:
- **Web Dashboard**: `http://<hub-ip>:8000`
- **Config**: `/etc/dragonfly/dragonfly.yaml`
- **Update**: `sudo dragonfly-update`
- **Uninstall**: `sudo dragonfly-uninstall`

---

## Adding Cameras

Edit `/etc/dragonfly/dragonfly.yaml` and add your camera's RTSP stream:

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
sudo systemctl restart dragonfly-capture dragonfly-portal
```

---

## Remote Access (Optional)

To view your cameras away from home without opening any router ports, install [Tailscale](https://tailscale.com) on your hub host. You can securely access the dashboard from your phone or laptop at `http://<tailscale-ip>:8000`.

---

## Compatibility

- **OS**: Debian 12+, Ubuntu 22.04+, Raspberry Pi OS, DietPi, or any Linux distro with `systemd`.
- **Architectures**: `x86_64` (Intel/AMD mini-PCs, NUCs, home servers), `aarch64` / `arm64` (Raspberry Pi 4/5, SBCs).
- **Storage**: External USB 3.0 HDD or SSD formatted as `ext4`.

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
