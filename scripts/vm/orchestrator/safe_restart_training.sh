#!/usr/bin/env bash
# Restart colonist-train (to pick up train.sh or loop changes) at the next safe
# point: once the next round's promotion match has begun and the early
# self-play of the round after it is done. The match's evaluation tasks end
# cleanly with their connections and the loop resumes the match after the
# restart. The early self-play runs during training and the match, so a
# restart at the match's start killed it: rounds 78 and 79 lost about 10
# minutes each to the hooks run again and the shards under way. Matches last
# up to 2 hours and early self-play about 20 minutes, so the wait is short;
# it gives up after 45 minutes. Returns at once; the wait runs in the
# background (log: data/orchestrator/jobs/restart-training.log).
set -euo pipefail
cd "$(dirname "$0")/../../.."

if [ "${1:-}" = --wait ]; then
  lines=$2
  until tail -n +$((lines + 1)) runs/train.log | grep -q 'promotion match ('; do sleep 2; done
  echo "match began $(date -u +%FT%TZ)"
  for _ in $(seq 270); do
    info=$(cat "$(ls -t runs/*/prefetch.json | head -1)" 2>/dev/null || true)
    case $info in *'"done": true'*) break ;; esac
    pid=$(echo "$info" | grep -o '"pid": [0-9]*' | grep -o '[0-9]*$' || true)
    [ -n "$pid" ] && [ -d "/proc/$pid" ] || break
    sleep 10
  done
  sudo systemctl restart colonist-train
  echo "restarted $(date -u +%FT%TZ)"
  exit 0
fi

mkdir -p data/orchestrator/jobs
lines=$(wc -l < runs/train.log)
nohup "$PWD/scripts/vm/orchestrator/safe_restart_training.sh" --wait "$lines" > data/orchestrator/jobs/restart-training.log 2>&1 < /dev/null &
echo $! > data/orchestrator/jobs/restart-training.pid
echo "colonist-train will restart once the next promotion match has begun and early self-play is done"
