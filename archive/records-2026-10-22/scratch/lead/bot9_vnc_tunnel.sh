#!/usr/bin/env bash
# One-off (owner's request, Oct 20, 10:43 AM): a private desktop with a fresh
# bot9 Chromium profile on colonist.io, reachable by a link from the owner's
# phone, to sign in the account that replaces Petra4620 (bot5). The transport
# is an HTTPS Cloudflare quick tunnel (random unguessable URL), VNC asks for a
# one-time password, and everything shuts itself down after 45 minutes.
# Copied from bot VM 2's bot8_vnc_tunnel.sh (Oct 19), profile and URL changed.
set -euo pipefail
cd "$HOME/colonist-ai"
ACCOUNT="${1:-bot9}"
OUT="$HOME/$ACCOUNT-login"
mkdir -p "$OUT" "$HOME/cloudflared-bin"
PROFILE="$HOME/.local/share/colonist-ai/$ACCOUNT-profile"
mkdir -p "$PROFILE"; chmod 700 "$PROFILE"

# cloudflared, downloaded into its own directory.
CF="$HOME/cloudflared-bin/cloudflared"
if [ ! -x "$CF" ]; then
  curl -sSL -o "$CF" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$CF"
fi
"$CF" --version > "$OUT/cloudflared-version.txt" 2>&1

# Clean slate (only our own desktop pieces; the bots run headless).
pkill -x Xvfb 2>/dev/null || true
pkill -x x11vnc 2>/dev/null || true
pkill -f websockify 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -f "user-data-dir=$PROFILE" 2>/dev/null || true
sleep 1

# One-time VNC password (shown to the owner in Discord, nowhere else).
PASS=$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(4))')
x11vnc -storepasswd "$PASS" "$OUT/vncpass" > /dev/null 2>&1
echo "$PASS" > "$OUT/vncpass.txt"; chmod 600 "$OUT/vncpass.txt"

setsid nohup Xvfb :99 -screen 0 1280x900x24 -nolisten tcp > "$OUT/xvfb.log" 2>&1 < /dev/null &
sleep 2
setsid nohup x11vnc -display :99 -localhost -rfbport 5900 -forever -shared -rfbauth "$OUT/vncpass" \
  -o "$OUT/x11vnc.log" > /dev/null 2>&1 < /dev/null &
setsid nohup websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 > "$OUT/websockify.log" 2>&1 < /dev/null &
export DISPLAY=:99
CHROME=$(find "$HOME/.cache/ms-playwright" \( -path '*/chrome-linux64/chrome' -o -path '*/chrome-linux/chrome' \) -type f | sort | tail -1)
setsid nohup "$CHROME" --user-data-dir="$PROFILE" --no-first-run --no-sandbox \
  --enable-webgl --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader \
  --window-size=1280,900 https://colonist.io/ > "$OUT/chrome.log" 2>&1 < /dev/null &
sleep 3
setsid nohup "$CF" tunnel --url http://127.0.0.1:6080 --no-autoupdate > "$OUT/cloudflared.log" 2>&1 < /dev/null &

# Auto shutdown after 45 minutes.
setsid nohup bash -c "sleep 2700; pkill -f 'cloudflared tunnel'; pkill -f websockify; pkill -x x11vnc; pkill -f 'user-data-dir=$PROFILE'; sleep 3; pkill -x Xvfb; echo closed \$(date -u) >> '$OUT/tunnel.log'" > /dev/null 2>&1 < /dev/null &

for i in $(seq 1 30); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$OUT/cloudflared.log" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 2
done
echo "URL=${URL:-none}"
echo "PASS=$PASS"
pgrep -x Xvfb > /dev/null && echo "xvfb ok"; pgrep -x x11vnc > /dev/null && echo "x11vnc ok"
pgrep -f websockify > /dev/null && echo "websockify ok"; pgrep -f "user-data-dir=$PROFILE" > /dev/null && echo "chrome ok"
