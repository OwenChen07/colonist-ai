#!/usr/bin/env bash
# The ranked bot runs on its own VM (COLONIST_BOT_HOST in scripts/vm/bot.env).
# From the coordinator:
#   scripts/vm/bot_ctl.sh status    services, runner status, last moves
#   scripts/vm/bot_ctl.sh restart   restart the ranked runner (only between games)
#   scripts/vm/bot_ctl.sh stop      stop the ranked runner
#   scripts/vm/bot_ctl.sh is-active | restarts | log
set -uo pipefail
cd "$(dirname "$0")/../.."
source scripts/vm/bot.env
KEY="${COLONIST_WORKER_SSH_KEY:-$HOME/.ssh/colonist_worker_ed25519}"
run() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$COLONIST_BOT_HOST" "$@"; }
case "${1:-status}" in
  status)
    run 'systemctl is-active colonist-ranked colonist-spectator; cat colonist-ai/data/ranked/runner-status.json; tail -5 colonist-ai/data/ranked/bridge.log' ;;
  is-active) run systemctl is-active colonist-ranked ;;
  restarts) run systemctl show -p NRestarts --value colonist-ranked ;;
  restart) run sudo systemctl restart colonist-ranked ;;
  stop) run sudo systemctl stop colonist-ranked ;;
  log) run 'tail -50 colonist-ai/data/ranked/bridge.log; tail -20 colonist-ai/data/ranked/ranked-run.log' ;;
  *) echo "usage: $0 status|is-active|restarts|restart|stop|log" >&2; exit 2 ;;
esac
