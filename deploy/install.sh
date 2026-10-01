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

show_banner() {
    local G=$'\033[1;32m'
    local P=$'\033[1;35m'
    local BOLD=$'\033[1m'
    local DIM=$'\033[2m'
    local R=$'\033[0m'

    echo ""
    echo "  ${P} ,-.  ,-.${R}"
    echo "  ${P} \\_ \\/ _/${R}   ${BOLD}Dragonfly${R} ${DIM}Hub Service Installer${R}"
    echo "  ${G}   )(   ${R}   Local-First Security & Camera Hub"
    echo "  ${P} _/ /\\ \\_${R}   ${DIM}https://github.com/charanuvs/dragonfly${R}"
    echo "  ${P} \`-'  \`-'${R}"
    echo ""
}

# Pixel-shaded intro (half-block rendering): a dragonfly hovering beside a daisy.
# Falls back to the plain emblem when not on a TTY or python3 is unavailable.
show_intro() {
    if [ ! -t 1 ] || [ "${TERM:-}" = "dumb" ] || [ "${DRAGONFLY_NO_ANIM:-0}" = "1" ] \
        || ! command -v python3 >/dev/null 2>&1; then
        show_banner
        return
    fi
    python3 - <<'PYEOF' || show_banner
import math, os, sys, time

W, H = 74, 32
FRAMES, DT = 90, 0.035
DX0, DY0 = 21.0, 13.5
FX, FY = 56.0, 13.0
TRUE = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def q256(r, g, b):
    if abs(r - g) < 12 and abs(g - b) < 12:
        if r < 8:
            return 16
        if r > 247:
            return 231
        return 232 + int(round((r - 8) / 239 * 23))
    return 16 + 36 * int(round(r / 255 * 5)) + 6 * int(round(g / 255 * 5)) + int(round(b / 255 * 5))


def sgr(c, layer):
    r, g, b = c
    return f"\033[{layer};2;{r};{g};{b}m" if TRUE else f"\033[{layer};5;{q256(r, g, b)}m"


def clamp(v):
    return max(0, min(255, int(v)))


def shade(c, f):
    return (clamp(c[0] * f), clamp(c[1] * f), clamp(c[2] * f))


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(clamp(a[i] + (b[i] - a[i]) * t) for i in range(3))


def put(c, x, y, col):
    if 0 <= x < W and 0 <= y < H:
        c[y][x] = col


def ellipse(c, cx, cy, rx, ry, colfn, angle=0.0):
    r = max(rx, ry) + 1
    ca, sa = math.cos(angle), math.sin(angle)
    for y in range(int(cy - r) - 1, int(cy + r) + 2):
        for x in range(int(cx - r) - 1, int(cx + r) + 2):
            dx, dy = x - cx, y - cy
            u = dx * ca + dy * sa
            v = -dx * sa + dy * ca
            d = (u / rx) ** 2 + (v / ry) ** 2
            if d <= 1.0:
                col = colfn(u, v, d)
                if col is not None:
                    put(c, x, y, col)


def draw_flower(c, t):
    sway = 0.05 * math.sin(t * 1.1)
    for y in range(int(FY + 10), H):
        xs = int(round(FX + 0.4 * math.sin(t * 0.6)))
        put(c, xs, y, (62, 132, 52))
        put(c, xs + 1, y, (40, 96, 36))
    ellipse(c, FX - 5.5, FY + 16.5, 4.2, 1.5,
            lambda u, v, d: (118, 190, 92) if abs(v) < 0.3 else mix((40, 108, 40), (92, 172, 72), (u + 4.2) / 8.4),
            angle=-0.55 + sway * 2)
    n = 14
    for i in range(n):
        a = 2 * math.pi * i / n + sway + 0.025 * math.sin(t * 1.7 + i)
        px, py = FX + 8.5 * math.cos(a), FY + 8.5 * math.sin(a)
        light = 0.5 + 0.5 * math.cos(a + 2.3)

        def petal(u, v, d, light=light):
            f = 0.86 + 0.14 * ((u + 5.5) / 11.0)
            f *= 0.94 + 0.06 * light
            if d > 0.80:
                f *= 0.86
            if abs(v) < 0.35 and u < 2.5:
                f *= 0.95
            return shade((255, 252, 240), f)

        ellipse(c, px, py, 5.5, 2.0, petal, angle=a)
    hx, hy = -1.6 + 0.4 * math.sin(t * 0.9), -1.6 + 0.3 * math.cos(t * 0.7)

    def core(u, v, d):
        col = mix((252, 212, 58), (176, 106, 16), d ** 0.75)
        if (int((u + 9) * 2.2) * 7 + int((v + 9) * 2.2) * 13) % 7 == 0:
            col = shade(col, 0.86)
        hd = (u - hx) ** 2 + (v - hy) ** 2
        if hd < 2.4:
            col = mix(col, (255, 249, 205), 1 - hd / 2.4)
        return col

    ellipse(c, FX, FY, 4.6, 4.6, core)
    k = int(t * 2.0) % n
    if (t * 2.0) % 1.0 < 0.5:
        a = 2 * math.pi * k / n + sway
        sx, sy = int(round(FX + 13.4 * math.cos(a))), int(round(FY + 13.4 * math.sin(a)))
        for ox, oy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            put(c, sx + ox, sy + oy, (255, 255, 255))


def draw_dragonfly(c, t, frame):
    dx = DX0 + 0.6 * math.sin(t * 0.7)
    dy = DY0 + 0.9 * math.sin(t * 1.3)
    up = frame % 2 == 0
    flap = 0.07 if up else -0.07
    blur = 0.70 if up else 1.0
    wscale = 0.82 if up else 1.0

    def wing(u, v, d, L, bl):
        if u > 0.72 * L and abs(v) < 0.55:
            return (46, 42, 68)
        base = mix((84, 88, 124), (140, 144, 186), d)
        if d > 0.86:
            base = (170, 174, 214)
        if int(u * 1.3) % 3 == 0 or abs(v) < 0.3:
            base = mix(base, (156, 156, 196), 0.5)
        return shade(base, bl)

    for side in (1, -1):
        for kind, ay, L, ry, base_a in (("fore", dy - 5.4, 9.2, 1.5, -0.26), ("hind", dy - 1.8, 8.6, 2.0, 0.30)):
            a = base_a + flap * (1.0 if kind == "fore" else -1.0)
            if side < 0:
                a = math.pi - a
            cx = dx + side * 1.4 + (L + 0.8) * math.cos(a)
            cy = ay + (L + 0.8) * math.sin(a)
            ellipse(c, cx, cy, L, ry * wscale, lambda u, v, d, L=L: wing(u, v, d, L, blur), angle=a)

    for side in (-1, 1):
        for ox, oy in ((2, -1.6), (3, -1.0), (2.5, -0.2), (3.5, 0.6)):
            put(c, int(round(dx + side * ox)), int(round(dy + oy)), (30, 60, 36))

    for i in range(15):
        y = dy + 0.5 + i
        dark = i % 3 == 2
        col = (26, 76, 40) if dark else mix((80, 172, 94), (44, 120, 60), i / 15)
        for xo in (-1, 0, 1):
            if abs(xo) <= 1.6 - i * 0.06:
                put(c, int(round(dx + xo)), int(round(y)), col if xo == 0 else shade(col, 0.70))
    put(c, int(round(dx)), int(round(dy + 15.5)), (30, 70, 40))

    ellipse(c, dx, dy - 3.6, 2.3, 3.3,
            lambda u, v, d: (122, 204, 132) if (abs(u) < 0.5 and v < 0) else mix((94, 188, 106), (34, 96, 46), d ** 0.7))
    ellipse(c, dx, dy - 7.4, 1.7, 1.4, lambda u, v, d: mix((70, 150, 80), (34, 90, 44), d))
    for side in (-1, 1):
        ellipse(c, dx + side * 1.7, dy - 7.9, 1.8, 1.7,
                lambda u, v, d, s=side: (152, 228, 238) if (u * s < -0.6 and v < -0.5) else mix((22, 98, 120), (8, 42, 58), d ** 0.6))


def render(c):
    out = []
    for y in range(0, H, 2):
        line = []
        for x in range(W):
            tp, bt = c[y][x], c[y + 1][x]
            if tp is None and bt is None:
                line.append("\033[0m ")
            elif bt is None:
                line.append("\033[0m" + sgr(tp, 38) + "▀")
            elif tp is None:
                line.append("\033[0m" + sgr(bt, 38) + "▄")
            else:
                line.append(sgr(tp, 38) + sgr(bt, 48) + "▀")
        out.append("".join(line) + "\033[0m")
    return "\n".join(out)


def main():
    out = sys.stdout
    rows = H // 2
    out.write("\033[?25l" + "\n" * rows)
    try:
        for f in range(FRAMES):
            t = f * DT
            c = [[None] * W for _ in range(H)]
            draw_flower(c, t)
            draw_dragonfly(c, t, f)
            out.write(f"\033[{rows}A" + render(c) + "\n")
            out.flush()
            time.sleep(DT)
    finally:
        out.write("\033[0m\033[?25h")
        out.flush()


main()
PYEOF
    echo ""
    echo "  $(printf '\033[1m')Dragonfly$(printf '\033[0m') $(printf '\033[2m')Hub Service Installer$(printf '\033[0m')"
    echo "  Local-First Security & Camera Hub"
    echo ""
}

show_intro

echo "========================================="
echo "   Platform:          $OS"
echo "   Install directory: $INSTALL_DIR"
echo "   Config directory:  $CONFIG_DIR"
echo "   Service user:      $SERVICE_USER"
echo "========================================="
echo ""

# 1. Determine artifact source (local files vs downloading GitHub release)
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

CLEANUP_TMP=""
trap 'printf "\033[?25h"; if [ -n "$CLEANUP_TMP" ] && [ -d "$CLEANUP_TMP" ]; then rm -rf "$CLEANUP_TMP"; fi' EXIT INT TERM

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
