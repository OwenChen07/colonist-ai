#!/usr/bin/env bash
# Startup script for the E42 training GPU VM (pytorch image). Like gcp_worker_startup.sh, but the
# venv sees the image's CUDA torch (--system-site-packages). Code arrives by rsync. Idempotent.
set -euo pipefail
USER_NAME=owen
ROOT=/home/$USER_NAME/colonist-ai
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  export DEBIAN_FRONTEND=noninteractive
  # The image has the venv module but not ensurepip, so a venv without python3.12-venv has no pip.
  apt-get update -q
  apt-get install -y -q python3.12-venv rsync
  rm -rf "$ROOT/.venv"
  sudo -u "$USER_NAME" mkdir -p "$ROOT/catanatron" "$ROOT/data" "$ROOT/models" "$ROOT/runs"
  sudo -u "$USER_NAME" python3 -m venv --system-site-packages "$ROOT/.venv"
  sudo -u "$USER_NAME" "$ROOT/.venv/bin/pip" install -q networkx click rich msgpack websockets
  site=$(sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  echo "$ROOT/catanatron" | sudo -u "$USER_NAME" tee "$site/catanatron-src.pth" >/dev/null
fi
