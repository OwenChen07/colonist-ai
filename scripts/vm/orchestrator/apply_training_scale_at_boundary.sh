#!/usr/bin/env bash
# Apply the next-round self-play/training scale without changing a round in
# progress. The watcher runs on the coordinator and waits for a new
# promotion-match log entry before changing systemd settings and restarting.
set -euo pipefail

cd "$HOME/colonist-ai"
mkdir -p data/orchestrator/jobs
exec 9>data/orchestrator/jobs/training-scale.lock
flock -n 9 || { echo "another training-scale watcher is already running"; exit 1; }

start_line=$(wc -l < runs/train.log)
echo "watching for promotion match after log line ${start_line} at $(date -u +%FT%TZ)"
until awk -v start="$start_line" 'NR > start && index($0, "promotion match (") { found=1; exit } END { exit !found }' runs/train.log; do
    sleep 2
done

env_file=scripts/vm/workers.env
cp -p "$env_file" "${env_file}.before-5000x4.$(date -u +%Y%m%dT%H%M%SZ)"
python3 - "$env_file" <<'PY'
import os
import sys
import tempfile

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()
values = {"SELFPLAY_GAMES": "5000", "TRAIN_EPOCHS": "4"}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else None
    if key in values:
        out.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in values.items():
    if key not in seen:
        out.append(f"{key}={value}")
directory = os.path.dirname(path)
fd, temp = tempfile.mkstemp(prefix=".workers.env.", dir=directory, text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(temp, os.stat(path).st_mode)
    os.replace(temp, path)
finally:
    if os.path.exists(temp):
        os.unlink(temp)
PY

echo "settings applied at safe boundary $(date -u +%FT%TZ); restarting colonist-train"
sudo systemctl restart colonist-train
for _ in $(seq 1 60); do
    if systemctl is-active --quiet colonist-train && \
       pgrep -af 'catanatron\.colonist\.loop runs/g1 .*--games 5000 .*--epochs 4 .*--gate confidence' >/dev/null; then
        echo "verified 5000 games, 4 epochs, confidence gate at $(date -u +%FT%TZ)"
        exit 0
    fi
    sleep 2
done
echo "service restarted but expected 5000/4 confidence-gate process was not verified" >&2
systemctl status colonist-train --no-pager >&2 || true
exit 1
