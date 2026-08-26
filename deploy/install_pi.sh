#!/usr/bin/env bash
# First-time install on the Raspberry Pi. Run from the repo root:
#   bash deploy/install_pi.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "== Installing system packages =="
sudo apt-get update
sudo apt-get install -y python3-venv mosquitto mosquitto-clients ffmpeg

echo "== Creating virtualenv =="
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[camera]"

echo "== Config =="
if [ ! -f config/dragonfly.yaml ]; then
    cp config/dragonfly.example.yaml config/dragonfly.yaml
    echo "Created config/dragonfly.yaml from the example — review it before going further."
fi

echo "== Checking external HDD mount =="
if ! mountpoint -q /mnt/dragonfly-hdd; then
    echo "WARNING: /mnt/dragonfly-hdd is not mounted. Recordings/database storage"
    echo "won't work until the external HDD is mounted there (add an fstab entry)."
fi

echo "== Installing systemd services =="
# Two independent services — capture (camera heartbeat/recorder/live-stream)
# and portal (web dashboard) — so restarting one never interrupts the other.
# See docs/architecture.md.
sudo cp deploy/dragonfly-capture.service /etc/systemd/system/dragonfly-capture.service
sudo cp deploy/dragonfly-portal.service /etc/systemd/system/dragonfly-portal.service

# Mount repair: clears the dead mount left behind when the recordings drive
# is physically unplugged, so it remounts cleanly when plugged back in and
# recording resumes by itself. See docs/recording.md.
#
# Installed to /usr/local/sbin root-owned, NOT run from the repo checkout.
# That matters: the sudoers rule below lets the unprivileged portal user run
# it without a password, so if the script lived somewhere that user could
# edit, the rule would amount to passwordless root.
sudo install -o root -g root -m 755 \
    deploy/dragonfly-mount-watchdog.sh /usr/local/sbin/dragonfly-mount-repair

# Scoped to exactly this one command, no arguments accepted.
sudo tee /etc/sudoers.d/dragonfly-mount-repair >/dev/null <<EOF
$(whoami) ALL=(root) NOPASSWD: /usr/local/sbin/dragonfly-mount-repair
EOF
sudo chmod 440 /etc/sudoers.d/dragonfly-mount-repair
# Reject a malformed sudoers file rather than leaving sudo broken.
sudo visudo -cf /etc/sudoers.d/dragonfly-mount-repair

# Timer stays as a backstop for when portal itself isn't running (the
# watchdog lives inside portal, so it can't repair anything while portal is
# down — e.g. a reboot where the drive comes back before portal does).
sudo cp deploy/dragonfly-mount-watchdog.service /etc/systemd/system/dragonfly-mount-watchdog.service
sudo cp deploy/dragonfly-mount-watchdog.timer /etc/systemd/system/dragonfly-mount-watchdog.timer

sudo systemctl daemon-reload
sudo systemctl enable dragonfly-capture dragonfly-portal
sudo systemctl restart dragonfly-capture dragonfly-portal
sudo systemctl enable --now dragonfly-mount-watchdog.timer

echo "== Done. Check status with:"
echo "     sudo systemctl status dragonfly-capture"
echo "     sudo systemctl status dragonfly-portal"
echo "     systemctl list-timers dragonfly-mount-watchdog.timer =="
