#!/usr/bin/env bash
# Every few minutes (cron on the coordinator): keep the ranked bot's VM in step.
#  - to the bot: this checkout's code, and runs/g1's state and nets, so its
#    runner switches to newly promoted nets between games;
#  - from the bot: its games and their training data (data/ranked, captures/
#    ranked), which training's live calibration and the watchdog read here.
# The bot VM is COLONIST_BOT_HOST in scripts/vm/bot.env.
set -uo pipefail
cd "$(dirname "$0")/../.."
source scripts/vm/bot.env
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20"
B="$COLONIST_BOT_HOST"
R=colonist-ai
exec 9>/tmp/sync_bot.lock
flock -n 9 || exit 0
rsync -a --exclude "*.egg-info" --exclude __pycache__ -e "$SSH" \
    catanatron bot scripts pyproject.toml README.md "$B:$R/"
$SSH "$B" "mkdir -p $R/runs/g1/nets $R/models/ranked $R/data/ranked $R/captures/ranked"
rsync -a -e "$SSH" runs/g1/nets/ "$B:$R/runs/g1/nets/"
rsync -a -e "$SSH" runs/g1/state.json "$B:$R/runs/g1/state.json"
# Every account's folders (data/ranked, data/<account>, ...), not the
# extension copies or keys.
rsync -a --exclude chrome-profile --exclude ".spectator-ingest-key" \
    --exclude "bot-extension-*" -e "$SSH" "$B:$R/data/" data/
rsync -a -e "$SSH" "$B:$R/captures/" captures/
