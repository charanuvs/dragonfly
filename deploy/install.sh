#!/usr/bin/env bash
# Dragonfly System Service Installer
#
# Installs Dragonfly as a native background service on Linux (systemd) or macOS (launchd).
# Can be executed directly from GitHub Releases:
#   curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | bash
#
# Or run from an extracted release tarball or git repository:
#   bash deploy/install.sh
set -euo pipefail

REPO="${DRAGONFLY_REPO:-charanuvs/dragonfly}"
VERSION="${DRAGONFLY_VERSION:-latest}"
SKIP_MOUNT="${DRAGONFLY_SKIP_MOUNT:-0}"
OS="$(uname -s)"

if [ "$OS" = "Linux" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "Error: This installer must be run as root (use sudo) on Linux." >&2
        exit 1
    fi
    INSTALL_DIR="${DRAGONFLY_INSTALL_DIR:-/opt/dragonfly}"
    CONFIG_DIR="${DRAGONFLY_CONFIG_DIR:-/etc/dragonfly}"
    SERVICE_USER="${DRAGONFLY_USER:-dragonfly}"
    SERVICE_GROUP="${DRAGONFLY_GROUP:-dragonfly}"
    MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-/mnt/dragonfly-hdd}"
    BIN_DIR="/usr/local/sbin"
    RUN_AS_USER=""
elif [ "$OS" = "Darwin" ]; then
    REAL_USER="${SUDO_USER:-$(id -un)}"
    USER_HOME="$(eval echo "~$REAL_USER")"
    INSTALL_DIR="${DRAGONFLY_INSTALL_DIR:-$USER_HOME/.local/share/dragonfly}"
    CONFIG_DIR="${DRAGONFLY_CONFIG_DIR:-$USER_HOME/.config/dragonfly}"
    SERVICE_USER="$REAL_USER"
    SERVICE_GROUP="$(id -gn "$REAL_USER")"
    MOUNT_POINT="${DRAGONFLY_MOUNT_POINT:-$INSTALL_DIR/recordings}"
    BIN_DIR="/usr/local/bin"
    RUN_AS_USER=""
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
        RUN_AS_USER="sudo -u $SUDO_USER"
    fi
else
    echo "Error: Unsupported operating system: $OS" >&2
    exit 1
fi

echo "========================================="
echo "   Dragonfly Hub Service Installer       "
echo "   Platform:          $OS                "
echo "   Install directory: $INSTALL_DIR       "
echo "   Config directory:  $CONFIG_DIR        "
echo "   Service user:      $SERVICE_USER      "
echo "========================================="
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

    CURL_AUTH=()
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        CURL_AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
    fi

    DOWNLOAD_SUCCESS=0
    if [ "$VERSION" = "latest" ]; then
        TARBALL_URL="https://github.com/$REPO/releases/latest/download/dragonfly.tar.gz"
        echo "Fetching release: $TARBALL_URL"
        if curl -fsSL -L "${CURL_AUTH[@]}" "$TARBALL_URL" -o "$TMP_DIR/dragonfly.tar.gz" 2>/dev/null; then
            DOWNLOAD_SUCCESS=1
        else
            echo "No tagged release found. Downloading latest main branch from $REPO..."
            TARBALL_URL="https://api.github.com/repos/$REPO/tarball/main"
            if [ ${#CURL_AUTH[@]} -eq 0 ]; then
                TARBALL_URL="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
            fi
            echo "Fetching: $TARBALL_URL"
            if curl -fsSL -L "${CURL_AUTH[@]}" "$TARBALL_URL" -o "$TMP_DIR/dragonfly.tar.gz" 2>/dev/null; then
                DOWNLOAD_SUCCESS=1
            fi
        fi
    else
        TARBALL_URL="https://github.com/$REPO/releases/download/$VERSION/dragonfly.tar.gz"
        echo "Fetching: $TARBALL_URL"
        if curl -fsSL -L "${CURL_AUTH[@]}" "$TARBALL_URL" -o "$TMP_DIR/dragonfly.tar.gz" 2>/dev/null; then
            DOWNLOAD_SUCCESS=1
        fi
    fi

    if [ "$DOWNLOAD_SUCCESS" -ne 1 ]; then
        echo "Error: Failed to download Dragonfly artifact from $REPO." >&2
        echo "If this repository is private, pass a GitHub token: GITHUB_TOKEN=... curl ... | sudo -E bash" >&2
        echo "Once the repository is public or has a release published, no token is required." >&2
        exit 1
    fi

    tar -xzf "$TMP_DIR/dragonfly.tar.gz" -C "$TMP_DIR"
    FOUND_SOURCE=$(find "$TMP_DIR" -maxdepth 2 -name "pyproject.toml" -exec dirname {} \; | head -n 1)
    if [ -n "$FOUND_SOURCE" ] && [ -d "$FOUND_SOURCE" ]; then
        SOURCE_DIR="$FOUND_SOURCE"
    elif [ -d "$TMP_DIR/dragonfly" ]; then
        SOURCE_DIR="$TMP_DIR/dragonfly"
    else
        SOURCE_DIR="$TMP_DIR"
    fi
fi

# 2. System dependencies
if [ "$OS" = "Linux" ]; then
    echo "== Installing system prerequisites (Linux apt) =="
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        mosquitto mosquitto-clients \
        ffmpeg util-linux e2fsprogs curl
elif [ "$OS" = "Darwin" ]; then
    echo "== Installing system prerequisites (macOS Homebrew) =="
    if ! command -v brew >/dev/null 2>&1; then
        if [ -x /opt/homebrew/bin/brew ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [ -x /usr/local/bin/brew ]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi
    if ! command -v brew >/dev/null 2>&1; then
        echo "Error: Homebrew is required on macOS to install dependencies (mosquitto, ffmpeg)." >&2
        echo "Please install Homebrew from https://brew.sh and re-run this script." >&2
        exit 1
    fi
    $RUN_AS_USER brew install mosquitto ffmpeg
    echo "Ensuring mosquitto broker is running via brew services..."
    $RUN_AS_USER brew services start mosquitto || true
fi

# 3. Create dedicated system user
if [ "$OS" = "Linux" ]; then
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
fi

# 4. Stop running services prior to file updates
echo "== Preparing application directory =="
if [ "$OS" = "Linux" ]; then
    systemctl stop dragonfly-capture dragonfly-portal 2>/dev/null || true
elif [ "$OS" = "Darwin" ]; then
    launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist" 2>/dev/null || true
    launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist" 2>/dev/null || true
fi

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/logs"
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
        if [ "$OS" = "Darwin" ]; then
            python3 -c "
import yaml
path = '$CONFIG_DIR/dragonfly.yaml'
try:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if 'storage' not in data:
        data['storage'] = {}
    data['storage']['recordings_path'] = '$INSTALL_DIR/recordings'
    data['storage']['database_path'] = '$INSTALL_DIR/dragonfly.db'
    data['storage']['mount_point'] = None
    with open(path, 'w') as f:
        yaml.safe_dump(data, f, sort_keys=False)
except Exception:
    pass
" 2>/dev/null || true
        fi
        echo "Created initial configuration at $CONFIG_DIR/dragonfly.yaml."
    else
        echo "Warning: No dragonfly.example.yaml found. You will need to create $CONFIG_DIR/dragonfly.yaml."
    fi
else
    echo "Existing configuration found at $CONFIG_DIR/dragonfly.yaml (preserved)."
fi

if [ "$OS" = "Linux" ]; then
    chown -R root:"$SERVICE_GROUP" "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    if [ -f "$CONFIG_DIR/dragonfly.yaml" ]; then
        chmod 640 "$CONFIG_DIR/dragonfly.yaml"
    fi
fi

# 6. Install mount repair watchdog script & CLI helpers
echo "== Installing CLI helpers =="
mkdir -p "$BIN_DIR" 2>/dev/null || sudo mkdir -p "$BIN_DIR"

if [ "$OS" = "Linux" ]; then
    install -o root -g root -m 755 \
        "$SOURCE_DIR/deploy/dragonfly-mount-watchdog.sh" /usr/local/sbin/dragonfly-mount-repair

    cat <<EOF > /etc/sudoers.d/dragonfly-mount-repair
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/dragonfly-mount-repair
EOF
    chmod 440 /etc/sudoers.d/dragonfly-mount-repair
    visudo -cf /etc/sudoers.d/dragonfly-mount-repair
fi

# Install restart helper
cp "$SOURCE_DIR/deploy/restart.sh" "$BIN_DIR/dragonfly-restart" 2>/dev/null || \
    sudo cp "$SOURCE_DIR/deploy/restart.sh" "$BIN_DIR/dragonfly-restart"
chmod +x "$BIN_DIR/dragonfly-restart" 2>/dev/null || sudo chmod +x "$BIN_DIR/dragonfly-restart"

# Install setup_storage helper
cp "$SOURCE_DIR/deploy/setup_storage.sh" "$BIN_DIR/dragonfly-setup-storage" 2>/dev/null || \
    sudo cp "$SOURCE_DIR/deploy/setup_storage.sh" "$BIN_DIR/dragonfly-setup-storage"
chmod +x "$BIN_DIR/dragonfly-setup-storage" 2>/dev/null || sudo chmod +x "$BIN_DIR/dragonfly-setup-storage"

# Install uninstaller helper
cp "$SOURCE_DIR/deploy/uninstall.sh" "$BIN_DIR/dragonfly-uninstall" 2>/dev/null || \
    sudo cp "$SOURCE_DIR/deploy/uninstall.sh" "$BIN_DIR/dragonfly-uninstall"
chmod +x "$BIN_DIR/dragonfly-uninstall" 2>/dev/null || sudo chmod +x "$BIN_DIR/dragonfly-uninstall"

# Install update CLI helper
cat <<'EOF' > "$BIN_DIR/dragonfly-update"
#!/usr/bin/env bash
set -euo pipefail
echo "== Checking for and applying latest Dragonfly update =="
curl -fsSL https://raw.githubusercontent.com/charanuvs/dragonfly/main/deploy/install.sh | DRAGONFLY_SKIP_MOUNT=1 bash
EOF
chmod 755 "$BIN_DIR/dragonfly-update" 2>/dev/null || sudo chmod 755 "$BIN_DIR/dragonfly-update"

# 7. Interactive USB Mount Setup
if [ "$SKIP_MOUNT" != "1" ]; then
    DRAGONFLY_MOUNT_POINT="$MOUNT_POINT" \
    DRAGONFLY_USER="$SERVICE_USER" \
    DRAGONFLY_GROUP="$SERVICE_GROUP" \
    DRAGONFLY_CONFIG="$CONFIG_DIR/dragonfly.yaml" \
    DRAGONFLY_INSTALL_DIR="$INSTALL_DIR" \
    "$BIN_DIR/dragonfly-setup-storage"
else
    echo "Skipping interactive storage setup (DRAGONFLY_SKIP_MOUNT=1)."
fi

# 8. Set ownership & background services installation
if [ "$OS" = "Linux" ]; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

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
elif [ "$OS" = "Darwin" ]; then
    echo "== Installing macOS LaunchAgents =="
    mkdir -p "$USER_HOME/Library/LaunchAgents"

    BREW_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if command -v brew >/dev/null 2>&1; then
        BREW_BIN="$(dirname "$(command -v brew)")"
        BREW_PATH="$BREW_BIN:$BREW_PATH"
    fi

    cat <<EOF > "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dragonfly.capture</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/venv/bin/dragonfly-capture</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/logs/capture.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/logs/capture.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>${BREW_PATH}</string>
        <key>DRAGONFLY_CONFIG</key>
        <string>${CONFIG_DIR}/dragonfly.yaml</string>
    </dict>
</dict>
</plist>
EOF

    cat <<EOF > "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dragonfly.portal</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/venv/bin/dragonfly-portal</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/logs/portal.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/logs/portal.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>${BREW_PATH}</string>
        <key>DRAGONFLY_CONFIG</key>
        <string>${CONFIG_DIR}/dragonfly.yaml</string>
    </dict>
</dict>
</plist>
EOF

    echo "Loading LaunchAgents..."
    $RUN_AS_USER launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist" 2>/dev/null || true
    $RUN_AS_USER launchctl unload "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist" 2>/dev/null || true
    $RUN_AS_USER launchctl load -w "$USER_HOME/Library/LaunchAgents/com.dragonfly.capture.plist"
    $RUN_AS_USER launchctl load -w "$USER_HOME/Library/LaunchAgents/com.dragonfly.portal.plist"

    echo ""
    echo "========================================="
    echo "   Dragonfly Installation Complete!      "
    echo "========================================="
    echo "Services active under launchd (macOS)."
fi

echo ""
echo "Useful Commands:"
echo "  Web Dashboard:   http://localhost:8000"
echo "  Configuration:   $CONFIG_DIR/dragonfly.yaml"
echo "  Storage Setup:   dragonfly-setup-storage"
echo "  Restart Service: dragonfly-restart"
echo "  Update Software: dragonfly-update"
echo "  Uninstall:       dragonfly-uninstall"
if [ "$OS" = "Linux" ]; then
    echo "  View Logs:       journalctl -u dragonfly-capture -u dragonfly-portal -f"
else
    echo "  View Logs:       tail -f $INSTALL_DIR/logs/*.log"
fi
echo "========================================="
