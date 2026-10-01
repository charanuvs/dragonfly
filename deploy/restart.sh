#!/usr/bin/env bash
# Restart Dragonfly service(s) on Linux (systemd) or macOS (launchd)
#
# Usage:
#   dragonfly-restart [both|capture|portal]
set -euo pipefail

TARGET="${1:-both}"
OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
    UID_NUM="$(id -u)"
    case "$TARGET" in
        both)
            echo "Restarting Dragonfly services (macOS launchd)..."
            launchctl kickstart -k "gui/$UID_NUM/com.dragonfly.capture" 2>/dev/null || \
                (launchctl unload ~/Library/LaunchAgents/com.dragonfly.capture.plist 2>/dev/null; launchctl load -w ~/Library/LaunchAgents/com.dragonfly.capture.plist) || true
            launchctl kickstart -k "gui/$UID_NUM/com.dragonfly.portal" 2>/dev/null || \
                (launchctl unload ~/Library/LaunchAgents/com.dragonfly.portal.plist 2>/dev/null; launchctl load -w ~/Library/LaunchAgents/com.dragonfly.portal.plist) || true
            ;;
        capture)
            echo "Restarting dragonfly-capture (macOS launchd)..."
            launchctl kickstart -k "gui/$UID_NUM/com.dragonfly.capture" 2>/dev/null || \
                (launchctl unload ~/Library/LaunchAgents/com.dragonfly.capture.plist 2>/dev/null; launchctl load -w ~/Library/LaunchAgents/com.dragonfly.capture.plist) || true
            ;;
        portal)
            echo "Restarting dragonfly-portal (macOS launchd)..."
            launchctl kickstart -k "gui/$UID_NUM/com.dragonfly.portal" 2>/dev/null || \
                (launchctl unload ~/Library/LaunchAgents/com.dragonfly.portal.plist 2>/dev/null; launchctl load -w ~/Library/LaunchAgents/com.dragonfly.portal.plist) || true
            ;;
        *)
            echo "Usage: dragonfly-restart [both|capture|portal]"
            exit 1
            ;;
    esac
    echo "Done."
else
    # Linux (systemd)
    case "$TARGET" in
        both) SERVICES="dragonfly-capture dragonfly-portal" ;;
        capture) SERVICES="dragonfly-capture" ;;
        portal) SERVICES="dragonfly-portal" ;;
        *) echo "Usage: dragonfly-restart [both|capture|portal]"; exit 1 ;;
    esac
    echo "Restarting $SERVICES (Linux systemd)..."
    sudo systemctl restart $SERVICES
    sleep 1
    sudo systemctl --no-pager status $SERVICES || true
fi
