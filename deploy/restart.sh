#!/usr/bin/env bash
# Restart Dragonfly service(s) on Linux (systemd) or macOS (launchd)
#
# Usage:
#   dragonfly-restart [all|capture|watchdog|detect|devices|dashboard|mqtt|mount-watchdog]
set -euo pipefail

TARGET="${1:-all}"
OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
    UID_NUM="$(id -u)"
    LABELS=()
    case "$TARGET" in
        all|both)
            brew services restart mosquitto
            LABELS=(capture watchdog detect devices dashboard)
            ;;
        capture|watchdog|detect|devices|dashboard) LABELS=("$TARGET") ;;
        mqtt)
            brew services restart mosquitto
            exit 0
            ;;
        mount-watchdog)
            echo "The mount watchdog timer is Linux-only."
            exit 1
            ;;
        *) echo "Usage: dragonfly-restart [all|capture|watchdog|detect|devices|dashboard|mqtt|mount-watchdog]"; exit 1 ;;
    esac
    for service in "${LABELS[@]}"; do
        label="com.dragonfly.dfly-$service"
        plist="$HOME/Library/LaunchAgents/$label.plist"
        launchctl kickstart -k "gui/$UID_NUM/$label" 2>/dev/null || {
            launchctl unload "$plist" 2>/dev/null || true
            launchctl load -w "$plist"
        }
    done
    echo "Done."
else
    # Linux (systemd)
    SERVICES=()
    case "$TARGET" in
        all|both)
            SERVICES=(dfly-mqtt dfly-capture dfly-detect dfly-devices dfly-dashboard dfly-watchdog dfly-mount-watchdog.timer)
            ;;
        capture|watchdog|detect|devices|dashboard|mqtt)
            SERVICES=("dfly-$TARGET")
            ;;
        mount-watchdog) SERVICES=(dfly-mount-watchdog.timer) ;;
        *) echo "Usage: dragonfly-restart [all|capture|watchdog|detect|devices|dashboard|mqtt|mount-watchdog]"; exit 1 ;;
    esac
    echo "Restarting ${SERVICES[*]} (Linux systemd)..."
    sudo systemctl restart "${SERVICES[@]}"
    sudo systemctl --no-pager status "${SERVICES[@]}" || true
fi
