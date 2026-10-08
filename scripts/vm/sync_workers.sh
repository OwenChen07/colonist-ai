#!/usr/bin/env bash
# Copy this checkout's code to every self-play worker before training starts,
# so all machines play the same rules and search. Workers keep their own venv
# (an editable install, so new source files are picked up).
set -uo pipefail
cd "$(dirname "$0")/../.."
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
ROOT="${COLONIST_WORKER_ROOT:-/home/owen/colonist-ai}"
IFS=',' read -ra HOSTS <<< "${COLONIST_WORKER_HOSTS:-}"
for item in "${HOSTS[@]}"; do
  host="${item%:*}"
  host="${host// /}"
  [ -n "$host" ] || continue
  if rsync -a --exclude "*.egg-info" --exclude __pycache__ -e "ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20" \
      catanatron pyproject.toml "$host:$ROOT/"; then
    echo "synced code to $host"
  else
    echo "could not sync code to $host; its shards will be played here"
  fi
done
exit 0
