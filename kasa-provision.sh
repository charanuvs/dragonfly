#!/usr/bin/env bash
# App-free Kasa Wi-Fi provisioning, adapted from the Reddit post below.
# Original author: dynastyuserd (2026), MIT License; notice retained below.
# https://www.reddit.com/r/TPLinkKasa/comments/1wlz37j/

set -u

PLUG_IP="192.168.0.1"
PREPARE_ONLY=false
if [ "${1:-}" = "--prepare" ]; then
  PREPARE_ONLY=true
fi

if command -v kasa >/dev/null 2>&1; then
  KASA=(kasa)
elif command -v uvx >/dev/null 2>&1; then
  KASA=(uvx --from python-kasa kasa)
elif command -v python3 >/dev/null 2>&1; then
  VENV="${HOME}/.cache/kasa-provision-venv"
  if [ ! -x "$VENV/bin/kasa" ]; then
    echo "Installing python-kasa in $VENV (one time)..."
    python3 -m venv "$VENV" || exit 1
    if ! "$VENV/bin/python" -m pip install python-kasa; then
      echo "Install failed. Reconnect to internet and run: bash kasa-provision.sh --prepare"
      exit 1
    fi
  fi
  KASA=("$VENV/bin/kasa")
else
  echo "Install Python 3, uv, or python-kasa first."
  exit 1
fi

if [ "$PREPARE_ONLY" = true ]; then
  echo "Prerequisite ready. Connect to the HS210 setup Wi-Fi, then run: bash kasa-provision.sh"
  exit 0
fi

read -r -p "Home Wi-Fi SSID (2.4 GHz WPA2): " SSID
read -r -s -p "Home Wi-Fi password: " WIFI_PASSWORD
printf '\n'
if [ -z "$SSID" ] || [ -z "$WIFI_PASSWORD" ]; then
  echo "Both the Wi-Fi SSID and password are required."
  exit 1
fi

probe_port() {
  python3 -c 'import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=1); s.close()' \
    "$PLUG_IP" "$1" >/dev/null 2>&1
}

reachable() {
  probe_port 9999 || probe_port 80
}

echo "On the Mac, join the HS210 setup Wi-Fi network."
echo "Stay connected to that network even if macOS reports no internet."
printf "Waiting for the switch"
until reachable; do
  printf "."
  sleep 2
done
printf '\nSwitch found at %s. Sending Wi-Fi settings...\n' "$PLUG_IP"

try_join() {
  local label="$1"
  shift
  local output

  if output=$("${KASA[@]}" --host "$PLUG_IP" --timeout 20 "$@" \
      wifi join "$SSID" --keytype "$KEYTYPE" --password "$WIFI_PASSWORD" \
      </dev/null 2>&1); then
    if printf '%s' "$output" | grep -q 'Response:' && \
        ! printf '%s' "$output" | grep -qiE 'error|failed|exception'; then
      echo "Accepted Wi-Fi settings using $label."
      return 0
    fi
  fi

  echo "The switch did not accept the $label attempt."
  return 1
}

KEYTYPE="3"
if ! try_join "legacy no-login"; then
  KEYTYPE="wpa2_psk"
  if ! try_join "Kasa setup credentials" --username kasa@tp-link.net --password kasaSetup; then
    if ! try_join "Tapo setup credentials" --username test@tp-link.net --password test; then
      echo "Provisioning failed. No switch state was changed by this script."
      exit 1
    fi
  fi
fi

echo "DONE. The HS210 is joining '$SSID'."
echo "Reconnect the Mac to your home Wi-Fi, then verify the switch is online."

# Copyright (c) 2026 dynastyuserd
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.