#!/usr/bin/env bash
# Uninstaller for Dragonfly service on Linux.
#
# Removes installed systemd units, helpers in /usr/local/sbin, sudoers rule,
# service user/group, and optionally configuration and storage mounts.
set -euo pipefail

INSTALL_DIR="${DRAGONFLY_INSTALL_DIR:-/opt/dragonfly}"
CONFIG_DIR="${DRAGONFLY_CONFIG_DIR:-/etc/dragonfly}"
SERVICE_USER="${DRAGONFLY_USER:-dragonfly}"
SERVICE_GROUP="${DRAGONFLY_GROUP:-dragonfly}"
MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-/mnt/dragonfly-hdd}"
OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
    REAL_USER="${SUDO_USER:-$(id -un)}"
    USER_HOME="$(eval echo "~$REAL_USER")"
    if [ ! -d "$INSTALL_DIR" ] && [ -d "$USER_HOME/.local/share/dragonfly" ]; then
        INSTALL_DIR="$USER_HOME/.local/share/dragonfly"
    fi
    CONFIG_DIR="${DRAGONFLY_CONFIG_DIR:-$USER_HOME/.config/dragonfly}"

    echo "========================================="
    echo "   Dragonfly Service Uninstaller (macOS) "
    echo "========================================="
    echo ""
    echo "This will stop and remove Dragonfly services from this Mac."
    if [ -c /dev/tty ] && [ -r /dev/tty ]; then
        read -r -p "Are you sure you want to proceed? [y/N]: " confirm </dev/tty || confirm="n"
    else
        read -r -p "Are you sure you want to proceed? [y/N]: " confirm
    fi
    case "$confirm" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Uninstall canceled."; exit 0 ;;
    esac

    echo "== Stopping and unloading LaunchAgents =="
    launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist" 2>/dev/null || true
    launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist" 2>/dev/null || true
    rm -f "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist"
    rm -f "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist"

    echo "== Removing CLI helpers =="
    rm -f /usr/local/bin/dragonfly-restart /usr/local/bin/dragonfly-update /usr/local/bin/dragonfly-uninstall /usr/local/bin/dragonfly-setup-storage 2>/dev/null || \
    sudo rm -f /usr/local/bin/dragonfly-restart /usr/local/bin/dragonfly-update /usr/local/bin/dragonfly-uninstall /usr/local/bin/dragonfly-setup-storage 2>/dev/null || true

    echo "== Removing application files ($INSTALL_DIR) =="
    rm -rf "$INSTALL_DIR" 2>/dev/null || sudo rm -rf "$INSTALL_DIR"

    # Optional config removal
    echo ""
    if [ -c /dev/tty ] && [ -r /dev/tty ]; then
        read -r -p "Do you also want to delete configuration in $CONFIG_DIR? [y/N]: " del_cfg </dev/tty || del_cfg="n"
    else
        read -r -p "Do you also want to delete configuration in $CONFIG_DIR? [y/N]: " del_cfg
    fi
    case "$del_cfg" in
        [yY]|[yY][eE][sS])
            rm -rf "$CONFIG_DIR"
            echo "Removed $CONFIG_DIR."
            ;;
        *)
            echo "Preserved $CONFIG_DIR."
            ;;
    esac

    echo ""
    echo "========================================="
    echo "   Dragonfly uninstalled successfully.   "
    echo "========================================="
    exit 0
fi

# Linux uninstaller below
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: dragonfly-uninstall must be run as root (use sudo)." >&2
    exit 1
fi

echo "========================================="
echo "   Dragonfly Service Uninstaller        "
echo "========================================="
echo ""
echo "This will stop and remove Dragonfly services from this system."
if [ -c /dev/tty ] && [ -r /dev/tty ]; then
    read -r -p "Are you sure you want to proceed? [y/N]: " confirm </dev/tty || confirm="n"
else
    read -r -p "Are you sure you want to proceed? [y/N]: " confirm
fi
case "$confirm" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Uninstall canceled."; exit 0 ;;
esac

echo "== Stopping and disabling services =="
systemctl stop dragonfly-capture dragonfly-portal dragonfly-mount-watchdog.timer dragonfly-mount-watchdog.service 2>/dev/null || true
systemctl disable dragonfly-capture dragonfly-portal dragonfly-mount-watchdog.timer dragonfly-mount-watchdog.service 2>/dev/null || true

echo "== Removing systemd units =="
rm -f /etc/systemd/system/dragonfly-capture.service
rm -f /etc/systemd/system/dragonfly-portal.service
rm -f /etc/systemd/system/dragonfly-mount-watchdog.service
rm -f /etc/systemd/system/dragonfly-mount-watchdog.timer
systemctl daemon-reload

echo "== Removing sudoers rule and helpers =="
rm -f /etc/sudoers.d/dragonfly-mount-repair
rm -f /usr/local/sbin/dragonfly-mount-repair
rm -f /usr/local/sbin/dragonfly-setup-storage
rm -f /usr/local/sbin/dragonfly-update

echo "== Removing application files =="
rm -rf "$INSTALL_DIR"

if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "== Removing service user ($SERVICE_USER) =="
    userdel "$SERVICE_USER" 2>/dev/null || true
fi
if getent group "$SERVICE_GROUP" >/dev/null; then
    groupdel "$SERVICE_GROUP" 2>/dev/null || true
fi

# Optional config removal
echo ""
read -r -p "Do you also want to delete configuration files in $CONFIG_DIR? [y/N]: " del_cfg
case "$del_cfg" in
    [yY]|[yY][eE][sS])
        rm -rf "$CONFIG_DIR"
        echo "Removed $CONFIG_DIR."
        ;;
    *)
        echo "Preserved $CONFIG_DIR."
        ;;
esac

# Optional fstab cleanup
echo ""
read -r -p "Do you want to unmount and remove $MOUNT_POINT entry from /etc/fstab? (Footage on disk is NOT deleted) [y/N]: " del_fstab
case "$del_fstab" in
    [yY]|[yY][eE][sS])
        if mountpoint -q "$MOUNT_POINT"; then
            umount "$MOUNT_POINT" 2>/dev/null || true
        fi
        sed -i "\|[[:space:]]${MOUNT_POINT}[[:space:]]|d" /etc/fstab
        echo "Removed $MOUNT_POINT from /etc/fstab."
        ;;
    *)
        echo "Kept /etc/fstab mount intact."
        ;;
esac

# Self-removal
rm -f /usr/local/sbin/dragonfly-uninstall

echo ""
echo "========================================="
echo "   Dragonfly uninstalled successfully.   "
echo "========================================="
