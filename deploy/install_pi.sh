#!/usr/bin/env bash
# First-time install on the Raspberry Pi. Run from the repo root:
#   bash deploy/install_pi.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "== Installing system packages =="
sudo apt-get update
sudo apt-get install -y python3-venv mosquitto mosquitto-clients

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

echo "== Installing systemd service =="
sudo cp deploy/dragonfly-hub.service /etc/systemd/system/dragonfly-hub.service
sudo systemctl daemon-reload
sudo systemctl enable dragonfly-hub
sudo systemctl restart dragonfly-hub

echo "== Done. Check status with: sudo systemctl status dragonfly-hub =="
