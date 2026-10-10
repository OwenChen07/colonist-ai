#!/usr/bin/env bash
# Start a private desktop with normal Chromium for a one-time Colonist sign-in.
# Tunnel port 5900 over SSH; never expose VNC to the public internet.
set -euo pipefail
cd "$(dirname "$0")/.."

Xvfb :99 -screen 0 1280x900x24 -nolisten tcp >/tmp/colonist-xvfb.log 2>&1 &
XVFB_PID=$!
cleanup() {
  kill "${CHROME_PID:-}" 2>/dev/null || true
  kill "$XVFB_PID" 2>/dev/null || true
  kill "$VNC_PID" 2>/dev/null || true
  kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT
sleep 1
x11vnc -display :99 -localhost -rfbport 5900 -forever -shared -nopw \
  -o /tmp/colonist-vnc.log >/dev/null 2>&1 &
VNC_PID=$!
websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 \
  >/tmp/colonist-websockify.log 2>&1 &
WEB_PID=$!
export DISPLAY=:99
echo 'Tunnel port 6080 over SSH, open http://localhost:6080/vnc.html?autoconnect=1, sign in, then press Enter here.'
CHROME=$(find "$HOME/.cache/ms-playwright" \( -path '*/chrome-linux-arm64/chrome' -o -path '*/chrome-linux/chrome' -o -path '*/chrome-linux64/chrome' \) -type f | sort | tail -1)
if [ -z "$CHROME" ]; then
  echo 'Chromium browser is missing; run setup_ranked_vm.sh first.' >&2
  exit 1
fi
# Usage: scripts/cloud_ranked_login.sh [ACCOUNT]  (default: ranked)
ACCOUNT="${1:-ranked}"
PROFILE="$HOME/.local/share/colonist-ai/${ACCOUNT}-profile"
mkdir -p "$PROFILE"
chmod 700 "$PROFILE"
"$CHROME" --user-data-dir="$PROFILE" --no-first-run --no-sandbox \
  --enable-webgl --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader \
  --disable-extensions-except="$PWD/bot" --load-extension="$PWD/bot" \
  https://colonist.io/ >/tmp/colonist-login-browser.log 2>&1 &
CHROME_PID=$!
read -r -p "Press Enter after the cloud browser shows the ${ACCOUNT} account signed in. "
kill "$CHROME_PID" 2>/dev/null || true
