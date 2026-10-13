#!/usr/bin/env bash
# Restart colonist-train (to pick up train.sh or loop changes) at the next safe
# point: when the next round's promotion match begins. Self-play is done then
# and the match's evaluation tasks end cleanly with their connections; the
# loop resumes the match after the restart. Returns at once; the wait runs in
# the background (log: data/orchestrator/jobs/restart-training.log).
set -euo pipefail
cd "$(dirname "$0")/../../.."
mkdir -p data/orchestrator/jobs
lines=$(wc -l < runs/train.log)
nohup bash -c "until tail -n +$((lines + 1)) runs/train.log | grep -q 'promotion match ('; do sleep 2; done; sudo systemctl restart colonist-train; echo \"restarted \$(date -u +%FT%TZ)\"" \
  > data/orchestrator/jobs/restart-training.log 2>&1 < /dev/null &
echo $! > data/orchestrator/jobs/restart-training.pid
echo "colonist-train will restart when the next promotion match begins"
