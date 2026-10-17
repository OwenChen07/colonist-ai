#!/usr/bin/env bash
# Startup script for a GCP spot self-play worker (passed with
# --metadata-from-file startup-script=scripts/vm/gcp_worker_startup.sh).
# It makes the user and venv distributed.py expects; the code itself arrives
# with sync_workers.sh, and a .pth file stands in for the editable install so
# the venv works before the first sync. Runs at every boot; idempotent.
set -euo pipefail
USER_NAME=owen
ROOT=/home/$USER_NAME/colonist-ai
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q python3-venv rsync
  sudo -u "$USER_NAME" mkdir -p "$ROOT/catanatron" "$ROOT/data" "$ROOT/models"
  sudo -u "$USER_NAME" python3 -m venv "$ROOT/.venv"
  sudo -u "$USER_NAME" "$ROOT/.venv/bin/pip" install -q networkx click rich msgpack numpy websockets
  site=$(sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  echo "$ROOT/catanatron" | sudo -u "$USER_NAME" tee "$site/catanatron-src.pth" >/dev/null
fi
