#!/usr/bin/env bash
# From the coordinator: copy what the OpenHex client VM needs. Usage:
#   scripts/vm/openhex/setup.sh HOST
# The code of the main checkout (~/colonist-ai, which land.sh keeps at
# main-9mnmbh; never a worktree), as sync_bot.sh copies it to the bot VMs,
# the net it plays (round 152 from Oct 20, lead's E20 switch; round 73 from
# Oct 18; round 66 before), and the API
# key (mode 600, never printed). The VM's startup.sh then installs and starts
# openhex-client.service; a changed unit needs `sudo cp` into
# /etc/systemd/system and `systemctl daemon-reload` at the restart.
# Only one client may play on the bot entry: a second one would pick up the
# first one's game. Stop any other client before the key reaches the VM.
# Running it again updates the code; the client picks it up at its next
# restart (touch data/openhex/pause there, wait for "paused" in its log,
# restart, rm the file).
set -euo pipefail
HOST="${1:?usage: setup.sh HOST}"
MAIN=/home/owen/colonist-ai
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
R=colonist-ai
$SSH "$HOST" "mkdir -p $R/runs/g1/nets $R/data/openhex/games"
rsync -a --exclude "*.egg-info" --exclude __pycache__ -e "$SSH" \
    "$MAIN/catanatron" "$MAIN/scripts" "$MAIN/pyproject.toml" "$HOST:$R/"
rsync -a -e "$SSH" "$MAIN/runs/g1/nets/round_152.npz" "$HOST:$R/runs/g1/nets/round_152.npz"
rsync -a --chmod=F600 -e "$SSH" "$MAIN/data/openhex/bot.json" "$HOST:$R/data/openhex/bot.json"
echo "setup: $HOST has the code, round 152 and the key"
