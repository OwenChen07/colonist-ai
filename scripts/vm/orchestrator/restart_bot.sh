#!/usr/bin/env bash
# Restart the Discord bot (colonist-orchestrator) so it picks up env-file
# changes: workstreams, worker count, budget, models. The lead's allowlist
# includes this script and nothing else that uses sudo. Running agents and
# their background jobs survive the restart (the service's KillMode=process).
# The restart waits a little, in the background, so the run that asked for it
# can finish its reply first. Returns at once.
set -euo pipefail
cd "$(dirname "$0")/../../.."
mkdir -p data/orchestrator/jobs
nohup bash -c "sleep ${1:-20}; sudo -n systemctl restart colonist-orchestrator; echo \"restarted \$(date -u +%FT%TZ)\"" \
  > data/orchestrator/jobs/restart-bot.log 2>&1 < /dev/null &
echo "colonist-orchestrator restarts in ${1:-20} s"
