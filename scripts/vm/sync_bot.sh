#!/usr/bin/env bash
# Every few minutes (cron on the coordinator): keep the ranked bot's VM in step.
#  - to the bot: this checkout's code, and runs/g1's state and nets, so its
#    runner switches to newly promoted nets between games;
#  - from the bot: its games and their training data (data/ranked, captures/
#    ranked), which training's live calibration and the watchdog read here.
# The bot VMs are in scripts/vm/bot.env: COLONIST_BOT_HOST (the ranked bot's)
# and COLONIST_BOT_HOSTS (all of them, space-separated).
set -uo pipefail
cd "$(dirname "$0")/../.."
source scripts/vm/bot.env
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20"
R=colonist-ai
exec 9>/tmp/sync_bot.lock
flock -n 9 || exit 0
# The current round's progress for the spectator site: the training log's end.
# Keep the entire current round, including any later fallback allocation.
# Six hundred lines could drop that marker and make progress exceed 100%.
tail -n 3000 runs/train.log > runs/g1/.train-tail.log 2>/dev/null \
  && mv runs/g1/.train-tail.log runs/g1/train-tail.log
# The loop's per-round settings for the site (train.sh reads them from
# workers.env, with these defaults).
epochs=$(sed -n 's/^TRAIN_EPOCHS=\([0-9]*\).*/\1/p' scripts/vm/workers.env 2>/dev/null | tail -1)
games=$(sed -n 's/^SELFPLAY_GAMES=\([0-9]*\).*/\1/p' scripts/vm/workers.env 2>/dev/null | tail -1)
printf '{"epochs": %s, "games": %s}\n' "${epochs:-3}" "${games:-3500}" > runs/g1/train-config.json
# The people model's refreshes and E54's progress for the site.
.venv/bin/python -m catanatron.colonist.spectator_extra --out data/spectator-extra.json 2>/dev/null
# Every bot VM: COLONIST_BOT_HOSTS (space-separated), or the one ranked host.
for B in ${COLONIST_BOT_HOSTS:-$COLONIST_BOT_HOST}; do
  rsync -a --exclude "*.egg-info" --exclude __pycache__ -e "$SSH" \
      catanatron bot scripts pyproject.toml README.md "$B:$R/" || continue
  $SSH "$B" "mkdir -p $R/runs/g1/nets $R/models/ranked $R/data $R/captures"
  rsync -a -e "$SSH" runs/g1/nets/ "$B:$R/runs/g1/nets/"
  rsync -a -e "$SSH" runs/g1/state.json "$B:$R/runs/g1/state.json"
  rsync -a -e "$SSH" runs/g1/train-tail.log "$B:$R/runs/g1/train-tail.log" 2>/dev/null
  rsync -a -e "$SSH" runs/g1/train-config.json "$B:$R/runs/g1/train-config.json" 2>/dev/null
  # The human opponent model (opponent_model.py refresh), for bots that use it.
  [ -e models/human/net.npz ] && $SSH "$B" "mkdir -p $R/models/human" \
      && rsync -a -e "$SSH" models/human/net.npz "$B:$R/models/human/net.npz"
  # Cloud credits for the site (scripts/credits.py on the owner's Mac).
  [ -e data/credits.json ] && rsync -a -e "$SSH" data/credits.json "$B:$R/data/credits.json"
  [ -e data/spectator-extra.json ] && rsync -a -e "$SSH" data/spectator-extra.json "$B:$R/data/spectator-extra.json"
  # Every account's folders (data/ranked, data/<account>, ...), not the
  # extension copies or keys.
  rsync -a --exclude chrome-profile --exclude ".spectator-ingest-key" \
      --exclude "bot-extension-*" -e "$SSH" "$B:$R/data/" data/
  rsync -a -e "$SSH" "$B:$R/captures/" captures/
done
