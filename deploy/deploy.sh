#!/usr/bin/env bash
# Run from your Mac after `git push`. Deploys the latest main branch to the
# Pi over Tailscale and restarts the service.
set -euo pipefail

HOST="${DRAGONFLY_HOST:-charan@phila}"

echo "== Deploying to $HOST =="
ssh "$HOST" bash -s <<'EOF'
set -euo pipefail
cd ~/dragonfly
git pull --ff-only
source .venv/bin/activate
pip install -e ".[camera]"
sudo systemctl restart dragonfly-hub
sleep 2
sudo systemctl --no-pager status dragonfly-hub
EOF

echo "== Tailing logs (Ctrl-C to stop) =="
ssh "$HOST" "journalctl -u dragonfly-hub -f -n 20"
