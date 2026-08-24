#!/usr/bin/env bash
# Run from your Mac after `git push`. Deploys the latest main branch to the
# Pi over Tailscale and restarts the service(s).
#
# Usage:
#   bash deploy/deploy.sh            # restart both capture + portal
#   bash deploy/deploy.sh portal     # restart only the dashboard
#   bash deploy/deploy.sh capture    # restart only camera capture
#
# capture and portal are independent services specifically so you can bounce
# the dashboard after a UI tweak without interrupting an in-progress
# recording, and vice versa — see docs/architecture.md.
set -euo pipefail

HOST="${DRAGONFLY_HOST:-charan@phila}"
TARGET="${1:-both}"

case "$TARGET" in
  both) SERVICES="dragonfly-capture dragonfly-portal" ;;
  capture) SERVICES="dragonfly-capture" ;;
  portal) SERVICES="dragonfly-portal" ;;
  *) echo "Usage: $0 [both|capture|portal]"; exit 1 ;;
esac

echo "== Deploying to $HOST ($SERVICES) =="
ssh "$HOST" bash -s <<EOF
set -euo pipefail
cd ~/dragonfly
git pull --ff-only
source .venv/bin/activate
pip install -e ".[camera]"
sudo systemctl restart $SERVICES
sleep 2
sudo systemctl --no-pager status $SERVICES
EOF

JOURNAL_ARGS=""
for s in $SERVICES; do JOURNAL_ARGS="$JOURNAL_ARGS -u $s"; done

echo "== Tailing logs (Ctrl-C to stop) =="
ssh "$HOST" "journalctl $JOURNAL_ARGS -f -n 20"
