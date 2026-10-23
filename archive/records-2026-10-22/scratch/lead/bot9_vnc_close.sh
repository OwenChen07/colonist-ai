#!/usr/bin/env bash
# Close the one-off sign-in desktop opened by bot9_vnc_tunnel.sh (the owner is done).
ACCOUNT="${1:-bot9}"
PROFILE="$HOME/.local/share/colonist-ai/$ACCOUNT-profile"
OUT="$HOME/$ACCOUNT-login"
pkill -x cloudflared 2>/dev/null
pkill -f "websockify --web" 2>/dev/null
pkill -x x11vnc 2>/dev/null
pkill -TERM -f "user-data-dir=$PROFILE" 2>/dev/null
sleep 4
pkill -KILL -f "user-data-dir=$PROFILE" 2>/dev/null
pkill -x Xvfb 2>/dev/null
rm -f "$OUT/vncpass" "$OUT/vncpass.txt"
echo "closed $(date -u)" >> "$OUT/tunnel.log"
sleep 1
pgrep -a "Xvfb|x11vnc|websockify|cloudflared|chrome" | cut -c1-100 || echo "all closed"
ls "$PROFILE/Default/Cookies" >/dev/null 2>&1 && echo "profile has cookies"
