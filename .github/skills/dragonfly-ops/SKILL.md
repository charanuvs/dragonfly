---
name: dragonfly-ops
description: 'Manage Dragonfly on Linux and macOS hosts: installation, storage setup, creating and adding camera configs (RTSP URLs, recording settings), restarting services, updating, and uninstallation.'
argument-hint: 'install | storage | camera | restart | update | uninstall'
user-invocable: true
disable-model-invocation: false
---

# Dragonfly Operations & Administration

This skill guides administration of Dragonfly on a Linux or macOS hub host, including service installation, storage mounting, camera configuration, service restarts, software updates, and uninstallation.

---

## 1. Installation

Dragonfly installs as an isolated background service (`systemd` on Linux, `launchd` on macOS) without requiring a git checkout.

### One-line Install from GitHub Releases
Run on your host:
```bash
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | bash
```
*(On Linux, run with `sudo bash`).*

### What the installer performs:
1. Installs system packages: `mosquitto` and `ffmpeg` via `apt` (Linux) or Homebrew (macOS).
2. Sets up Python virtual environment and installs Dragonfly with camera drivers.
3. Configures initial settings at `/etc/dragonfly/dragonfly.yaml` (Linux) or `~/.config/dragonfly/dragonfly.yaml` (macOS).
4. Installs unified CLI commands (`dragonfly-restart`, `dragonfly-setup-storage`, `dragonfly-update`, `dragonfly-uninstall`).
5. Runs the interactive storage setup prompt.
6. Starts and enables the background services.

---

## 2. Storage Setup & Mounting

Recordings and SQLite database files use dedicated external storage mounted at `/mnt/dragonfly-hdd` to keep continuous high-write video off the primary operating system disk.

### Interactive Storage Setup
Run anytime as root:
```bash
sudo dragonfly-setup-storage
```

### Storage Automation Details:
- **Device Detection**: Scans block devices on the USB bus, excluding root (`/`), boot (`/boot`), and firmware partitions.
- **Formatting**: Offers ext4 formatting with filesystem label `dragonfly-data`.
- **fstab Persistence**: Resolves the partition's filesystem UUID and writes:
  ```fstab
  UUID=<uuid>  /mnt/dragonfly-hdd  ext4  defaults,nofail,noatime  0  2
  ```
  *The `nofail` flag prevents the system from stalling in emergency mode on boot if the drive is unplugged.*
- **Ownership**: Automatically runs `chown -R dragonfly:dragonfly /mnt/dragonfly-hdd` and `chmod 755`.
- **Mount Watchdog**: If the USB drive is unplugged or replugged, the mount repair watchdog (`deploy/dragonfly-mount-watchdog.sh`) automatically cleans dead mounts and remounts via `mount -a`.

---

## 3. Creating & Adding Camera Configurations

Cameras are configured in `/etc/dragonfly/dragonfly.yaml` under the `cameras:` list.

### Configuration Procedure
1. Open configuration on the hub host:
   ```bash
   sudo nano /etc/dragonfly/dragonfly.yaml
   ```
2. Add or adjust a camera block under `cameras:`:
   ```yaml
   cameras:
     - id: front_door                # Unique alphanumeric identifier
       name: "Front Door"            # Friendly display name
       rtsp_url: "rtsp://user:pass@192.168.1.50:554/stream1"
       poll_interval_s: 15          # Watchdog polling frequency
       timeout_s: 3

       # Recording configuration
       record: true
       recording_rtsp_url: null     # Set to lower-res substream if camera supports it
       fps: 10                      # Target re-encoded FPS
       bitrate_kbps: 200            # Target bitrate (200 kbps ~ 2.1 GB/day)
       segment_seconds: 300         # 5-minute video chunks
       overlap_seconds: 10          # Gapless buffer overlap
       high_watermark_pct: 90.0     # Delete oldest file when disk >= 90%
       low_watermark_pct: 80.0      # Stop deletion when disk <= 80%
       retention_days: null         # Optional fixed day ceiling (null = driven by watermarks)
   ```
3. Consult [Camera RTSP Guide](./references/camera-rtsp-guide.md) for brand-specific RTSP URLs (Tapo, Amcrest, Reolink, Hikvision, etc.) and password encoding rules.
4. Save file and restart services (see below).

---

## 4. Restarting Services

Configuration changes (adding, modifying, or removing cameras) require restarting the services to take effect.

### Unified Command (Linux & macOS):
```bash
dragonfly-restart
```
Or selectively:
```bash
dragonfly-restart capture   # Camera capture & recorder only
dragonfly-restart portal    # Web dashboard only
```

### Native Service Commands:
- **Linux (systemd)**:
  ```bash
  sudo systemctl restart dragonfly-capture dragonfly-portal
  ```
- **macOS (launchd)**:
  ```bash
  launchctl kickstart -k gui/$UID/com.dragonfly.capture
  launchctl kickstart -k gui/$UID/com.dragonfly.portal
  ```

### Verifying Service Status & Logs:
```bash
# On Linux:
sudo systemctl status dragonfly-capture dragonfly-portal
journalctl -u dragonfly-capture -u dragonfly-portal -f -n 50

# On macOS:
launchctl list | grep dragonfly
tail -f ~/.local/share/dragonfly/logs/*.log
```

---

## 5. Updating Dragonfly

To upgrade Dragonfly to the latest GitHub release without losing `/etc/dragonfly/dragonfly.yaml` or `/mnt/dragonfly-hdd`:

```bash
sudo dragonfly-update
```

Or re-run the release installer:
```bash
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | sudo bash
```

The updater pulls the latest release wheel, updates `/opt/dragonfly/venv`, updates systemd service units, and restarts the services. Existing configuration and storage mounts remain untouched.

---

## 6. Uninstalling Dragonfly

To remove Dragonfly from the hub host:

```bash
sudo dragonfly-uninstall
```

### What the uninstaller removes:
- Stops and disables `dragonfly-capture`, `dragonfly-portal`, and `dragonfly-mount-watchdog.timer`.
- Removes `/etc/systemd/system/dragonfly-*`.
- Removes `/etc/sudoers.d/dragonfly-mount-repair`.
- Removes helper scripts from `/usr/local/sbin/`.
- Deletes application directory `/opt/dragonfly/` and removes system user/group `dragonfly`.
- Prompts whether to keep or remove `/etc/dragonfly` (configuration).
- Prompts whether to unmount and remove `/mnt/dragonfly-hdd` from `/etc/fstab` (recorded video footage on the drive is preserved).
