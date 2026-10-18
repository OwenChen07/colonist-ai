#!/usr/bin/env bash
# Startup script for the OpenHex client VM (openhex-bot-1), passed with
# --metadata-from-file startup-script=scripts/vm/openhex/startup.sh. Runs as
# root at every boot; idempotent. It makes the user and venv, then waits for
# setup.sh (run on the coordinator) to copy the code, the net and the key, and
# installs and starts openhex-client.service from the copied repo.
set -euo pipefail
USER_NAME=owen
ROOT=/home/$USER_NAME/colonist-ai
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q python3-venv rsync
  sudo -u "$USER_NAME" mkdir -p "$ROOT/catanatron" "$ROOT/data/openhex/games" "$ROOT/runs/g1/nets"
  sudo -u "$USER_NAME" python3 -m venv "$ROOT/.venv"
  sudo -u "$USER_NAME" "$ROOT/.venv/bin/pip" install -q networkx click rich msgpack numpy websockets
  site=$(sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  echo "$ROOT/catanatron" | sudo -u "$USER_NAME" tee "$site/catanatron-src.pth" >/dev/null
fi
# A transient unit, so the wait outlives this script (up to 2 h).
UNIT="$ROOT/scripts/vm/openhex/openhex-client.service"
systemctl stop openhex-install 2>/dev/null || true
systemd-run --unit=openhex-install /bin/bash -c "
  for _ in \$(seq 720); do
    if [ -f $UNIT ] && [ -f $ROOT/runs/g1/nets/round_073.npz ] && [ -f $ROOT/data/openhex/bot.json ]; then
      cp $UNIT /etc/systemd/system/openhex-client.service
      systemctl daemon-reload
      systemctl enable --now openhex-client.service
      exit 0
    fi
    sleep 10
  done"
