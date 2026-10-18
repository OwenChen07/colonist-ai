#!/usr/bin/env bash
# On the coordinator: copy the OpenHex VM's games and log back, so tally.py
# reads the coordinator's data/openhex/games/ as before. Usage:
#   scripts/vm/openhex/sync_games.sh HOST [--loop]
# --loop repeats every 5 minutes (run it with nohup as a job). --update keeps
# a newer copy here (a game the coordinator's own client played).
set -uo pipefail
HOST="${1:?usage: sync_games.sh HOST [--loop]}"
MAIN=/home/owen/colonist-ai
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
R=colonist-ai
while :; do
  rsync -a --update -e "$SSH" "$HOST:$R/data/openhex/games/" "$MAIN/data/openhex/games/" \
    && rsync -a -e "$SSH" "$HOST:$R/data/openhex/client.log" "$MAIN/data/openhex/vm-client.log" \
    && echo "$(TZ=America/Toronto date '+%b %d %I:%M %p') synced from $HOST" \
    || echo "$(TZ=America/Toronto date '+%b %d %I:%M %p') could not sync from $HOST"
  [ "${2:-}" = "--loop" ] || break
  sleep 300
done
