#!/usr/bin/env bash
# Restart one bot account's service (colonist-casual@ACCOUNT) between games:
# on its bot VM, wait until its runner records the end of the current game,
# then restart. A runner that is stuck (needs_attention) or not running is
# restarted at once. Returns at once; the wait runs on the bot VM (log:
# data/ACCOUNT/restart-orchestrator.log there).
set -euo pipefail
cd "$(dirname "$0")/../../.."
account="${1:?usage: safe_restart_account.sh ACCOUNT}"
source scripts/vm/bot.env
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
for host in ${COLONIST_BOT_HOSTS:-$COLONIST_BOT_HOST}; do
  ssh -i "$KEY" -o BatchMode=yes "$host" "test -e colonist-ai/scripts/vm/bot/accounts/$account.env" || continue
  ssh -i "$KEY" -o BatchMode=yes "$host" "cd colonist-ai; f=data/$account/runner-status.json
    state=\$(python3 -c \"import json;print(json.load(open('\$f')).get('state',''))\" 2>/dev/null || echo none)
    if [ \"\$state\" != running ] && [ \"\$state\" != starting ] || ! systemctl is-active --quiet colonist-casual@$account; then
      sudo systemctl restart colonist-casual@$account; echo \"restarted now (runner state \$state)\"
    else
      nohup bash -c \"t=\\\$(stat -c %Y \$f); while [ \\\$(stat -c %Y \$f) = \\\$t ]; do sleep 3; done; sudo systemctl restart colonist-casual@$account; echo restarted \\\$(date -u +%FT%TZ)\" > data/$account/restart-orchestrator.log 2>&1 < /dev/null &
      echo \"$account restarts when its current game ends\"
    fi"
  exit 0
done
echo "no bot VM has account $account" >&2
exit 1
