#!/usr/bin/env bash
# Set up a fresh bot VM (Ubuntu 24.04, user ubuntu) for one account. Run on the
# VM after the coordinator's first sync_bot.sh has copied the code over:
#
#   cd ~/colonist-ai && scripts/vm/bot/provision.sh ACCOUNT PORT
#
# Installs the system packages, .venv (CPU torch, the colonist and automation
# extras), Playwright's Chromium, a local-only display for the owner's sign-in,
# and the account's service (installed but not started: the owner signs in
# first). Safe to rerun.
#
# Sign-in (the owner, from the coordinator):
#   ssh -i ~/.ssh/colonist_worker_ed25519 -L 5901:localhost:5901 ubuntu@HOST
#   then on the VM: scripts/vm/bot/provision.sh login ACCOUNT
#   and point a VNC viewer at localhost:5901. The display listens on the VM's
#   localhost only, so it is reachable through the ssh tunnel alone.
set -euo pipefail
cd "$(dirname "$0")/../../.."

if [ "${1:-}" = "login" ]; then
  ACCOUNT=${2:?usage: provision.sh login ACCOUNT}
  pgrep -x Xvfb >/dev/null || (Xvfb :1 -screen 0 1280x800x24 &>/tmp/xvfb.log &)
  sleep 1
  pgrep -x x11vnc >/dev/null \
    || x11vnc -display :1 -localhost -rfbport 5901 -forever -quiet -bg -o /tmp/x11vnc.log
  DISPLAY=:1 .venv/bin/python -m catanatron.colonist.ranked_runner \
    --account "$ACCOUNT" --login-only
  exit 0
fi

ACCOUNT=${1:?usage: provision.sh ACCOUNT PORT}
PORT=${2:?usage: provision.sh ACCOUNT PORT}

sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
  python3-venv python3-dev build-essential rsync xvfb x11vnc
[ -d .venv ] || python3 -m venv .venv
PIP=".venv/bin/python -m pip"
$PIP install -q --upgrade pip
$PIP install -q torch --index-url https://download.pytorch.org/whl/cpu
$PIP install -q -e ".[colonist,automation,train]"
.venv/bin/python -m playwright install --with-deps chromium

mkdir -p "data/$ACCOUNT" "models/$ACCOUNT" captures runs/g1/nets scripts/vm/bot/accounts
env_file="scripts/vm/bot/accounts/$ACCOUNT.env"
[ -e "$env_file" ] || printf 'PORT=%s\n' "$PORT" > "$env_file"
sudo install -m 644 scripts/vm/bot/colonist-casual@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "colonist-casual@$ACCOUNT"
free -m
echo "Ready. Put the net at models/$ACCOUNT/model.npz, sign in (provision.sh login $ACCOUNT),"
echo "then: sudo systemctl start colonist-casual@$ACCOUNT"
