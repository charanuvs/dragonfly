#!/usr/bin/env bash
# Dragonfly System Service Installer
#
# Installs Dragonfly as a native systemd service on Linux.
# Can be executed directly from GitHub Releases:
#   curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | sudo bash
#
# Or run from an extracted release tarball or git repository:
#   sudo bash deploy/install.sh
set -euo pipefail

REPO="${DRAGONFLY_REPO:-charanuvs/dragonfly}"
VERSION="${DRAGONFLY_VERSION:-latest}"
INSTALL_DIR="${DRAGONFLY_INSTALL_DIR:-/opt/dragonfly}"
CONFIG_DIR="${DRAGONFLY_CONFIG_DIR:-/etc/dragonfly}"
SERVICE_USER="${DRAGONFLY_USER:-dragonfly}"
SERVICE_GROUP="${DRAGONFLY_GROUP:-dragonfly}"
MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-/mnt/dragonfly-hdd}"
SKIP_MOUNT="${DRAGONFLY_SKIP_MOUNT:-0}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This installer must be run as root (use sudo)." >&2
    exit 1
fi

# Reopen stdin to /dev/tty if stdin is piped (e.g. curl ... | sudo bash)
if [ ! -t 0 ] && [ -e /dev/tty ] && [ -r /dev/tty ]; then
    exec 0< /dev/tty
fi

echo "========================================="
echo "   Dragonfly Hub Service Installer       "
echo "========================================="
echo "Install directory: $INSTALL_DIR"
echo "Config directory:  $CONFIG_DIR"
echo "Service user:      $SERVICE_USER"
echo ""

# 1. Determine artifact source (local files vs downloading GitHub release)
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

CLEANUP_TMP=""
trap 'if [ -n "$CLEANUP_TMP" ] && [ -d "$CLEANUP_TMP" ]; then rm -rf "$CLEANUP_TMP"; fi' EXIT

SOURCE_DIR=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../pyproject.toml" ]; then
    # Running from source checkout or unpacked release
    SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    echo "Installing from local source: $SOURCE_DIR"
else
    echo "Downloading Dragonfly ($VERSION) from GitHub ($REPO)..."
    TMP_DIR=$(mktemp -d)
    CLEANUP_TMP="$TMP_DIR"

    if [ "$VERSION" = "latest" ]; then
        TARBALL_URL="https://github.com/$REPO/releases/latest/download/dragonfly.tar.gz"
    else
        TARBALL_URL="https://github.com/$REPO/releases/download/$VERSION/dragonfly.tar.gz"
    fi

    echo "Fetching: $TARBALL_URL"
    if ! curl -fsSL -L "$TARBALL_URL" -o "$TMP_DIR/dragonfly.tar.gz"; then
        echo "Error: Failed to download release artifact from $TARBALL_URL." >&2
        echo "Please verify the repository name and release tag." >&2
        exit 1
    fi

    tar -xzf "$TMP_DIR/dragonfly.tar.gz" -C "$TMP_DIR"
    if [ -d "$TMP_DIR/dragonfly" ]; then
        SOURCE_DIR="$TMP_DIR/dragonfly"
    else
        SOURCE_DIR="$TMP_DIR"
    fi
fi

# 2. System dependencies
echo "== Installing system prerequisites =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    mosquitto mosquitto-clients \
    ffmpeg util-linux e2fsprogs curl

# 3. Create dedicated system user
echo "== Setting up service user ($SERVICE_USER) =="
if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd -r "$SERVICE_GROUP"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd -r -g "$SERVICE_GROUP" -s /usr/sbin/nologin -d "$INSTALL_DIR" -c "Dragonfly Hub Service" "$SERVICE_USER"
fi
# Add to video group if present (for camera hardware / GPU access)
if getent group video >/dev/null; then
    usermod -a -G video "$SERVICE_USER"
fi

# 4. Stop running services prior to file updates
echo "== Preparing application directory =="
systemctl stop dragonfly-capture dragonfly-portal 2>/dev/null || true

mkdir -p "$INSTALL_DIR"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi

"$INSTALL_DIR/venv/bin/pip" install --upgrade pip

# Find wheel or install from directory
WHEEL_FILE=$(find "$SOURCE_DIR" -maxdepth 2 -name "dragonfly-*.whl" 2>/dev/null | head -n 1 || true)
if [ -n "$WHEEL_FILE" ]; then
    echo "Installing wheel: $WHEEL_FILE"
    "$INSTALL_DIR/venv/bin/pip" install "${WHEEL_FILE}[camera]"
else
    echo "Installing from source directory: $SOURCE_DIR"
    "$INSTALL_DIR/venv/bin/pip" install "${SOURCE_DIR}[camera]"
fi

# 5. Configuration directory and default config
echo "== Checking configuration =="
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/dragonfly.yaml" ]; then
    if [ -f "$SOURCE_DIR/config/dragonfly.example.yaml" ]; then
        cp "$SOURCE_DIR/config/dragonfly.example.yaml" "$CONFIG_DIR/dragonfly.yaml"
        echo "Created initial configuration at $CONFIG_DIR/dragonfly.yaml from example."
    else
        echo "Warning: No dragonfly.example.yaml found. You will need to create $CONFIG_DIR/dragonfly.yaml."
    fi
else
    echo "Existing configuration found at $CONFIG_DIR/dragonfly.yaml (preserved)."
fi

chown -R root:"$SERVICE_GROUP" "$CONFIG_DIR"
chmod 750 "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/dragonfly.yaml" ]; then
    chmod 640 "$CONFIG_DIR/dragonfly.yaml"
fi

# 6. Install mount repair watchdog script & sudoers
echo "== Installing mount repair watchdog =="
install -o root -g root -m 755 \
    "$SOURCE_DIR/deploy/dragonfly-mount-watchdog.sh" /usr/local/sbin/dragonfly-mount-repair

cat <<EOF > /etc/sudoers.d/dragonfly-mount-repair
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/dragonfly-mount-repair
EOF
chmod 440 /etc/sudoers.d/dragonfly-mount-repair
visudo -cf /etc/sudoers.d/dragonfly-mount-repair

# Install setup_storage helper
install -o root -g root -m 755 \
    "$SOURCE_DIR/deploy/setup_storage.sh" /usr/local/sbin/dragonfly-setup-storage

# Install update CLI helper
cat <<'EOF' > /usr/local/sbin/dragonfly-update
#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: dragonfly-update must be run as root (use sudo)." >&2
    exit 1
fi
echo "== Checking for and applying latest Dragonfly update =="
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | DRAGONFLY_SKIP_MOUNT=1 bash
EOF
chmod 755 /usr/local/sbin/dragonfly-update

# Install uninstaller helper
install -o root -g root -m 755 \
    "$SOURCE_DIR/deploy/uninstall.sh" /usr/local/sbin/dragonfly-uninstall

# 7. Interactive USB Mount Setup
if [ "$SKIP_MOUNT" != "1" ]; then
    DRAGONFLY_MOUNT_POINT="$MOUNT_POINT" \
    DRAGONFLY_USER="$SERVICE_USER" \
    DRAGONFLY_GROUP="$SERVICE_GROUP" \
    /usr/local/sbin/dragonfly-setup-storage
else
    echo "Skipping interactive storage setup (DRAGONFLY_SKIP_MOUNT=1)."
fi

# 8. Set ownership
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

# 9. Systemd services installation
echo "== Installing systemd services =="
cp "$SOURCE_DIR/deploy/dragonfly-capture.service" /etc/systemd/system/dragonfly-capture.service
cp "$SOURCE_DIR/deploy/dragonfly-portal.service" /etc/systemd/system/dragonfly-portal.service
cp "$SOURCE_DIR/deploy/dragonfly-mount-watchdog.service" /etc/systemd/system/dragonfly-mount-watchdog.service
cp "$SOURCE_DIR/deploy/dragonfly-mount-watchdog.timer" /etc/systemd/system/dragonfly-mount-watchdog.timer

systemctl daemon-reload
systemctl enable dragonfly-capture dragonfly-portal dragonfly-mount-watchdog.timer
systemctl restart dragonfly-capture dragonfly-portal
systemctl restart dragonfly-mount-watchdog.timer

echo ""
echo "========================================="
echo "   Dragonfly Installation Complete!      "
echo "========================================="
echo "Status:"
systemctl --no-pager status dragonfly-capture dragonfly-portal dragonfly-mount-watchdog.timer || true

echo ""
echo "Useful Commands:"
echo "  Web Dashboard:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<host-ip>'):8000"
echo "  Configuration:   $CONFIG_DIR/dragonfly.yaml"
echo "  Storage Setup:   sudo dragonfly-setup-storage"
echo "  Update Software: sudo dragonfly-update"
echo "  Uninstall:       sudo dragonfly-uninstall"
echo "  View Logs:       journalctl -u dragonfly-capture -u dragonfly-portal -f"
echo "========================================="
