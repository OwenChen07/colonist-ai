#!/usr/bin/env bash
# Weekly (cron on the coordinator): add new games against people to the
# human-game training set, data/human (human_dataset.py; it skips captures it
# has already indexed). train.sh reads data/human/train/value each round, and
# self-play starts a share of its games from data/human/starts.jsonl.gz
# (starts.py, rebuilt here from the training split).
set -euo pipefail
cd "$(dirname "$0")/../.."
nice -n 19 .venv/bin/python -m catanatron.colonist.human_dataset \
    "captures/*/bridge-*.json" --games "data/*/games.jsonl" --out data/human \
    --own-accounts CatBot77,Ericha8499,Dilly6283,Sherie3424,Petra4620 \
    >> data/human/build.log 2>&1
exec nice -n 19 .venv/bin/python -m catanatron.colonist.starts data/human \
    --out data/human/starts.jsonl.gz >> data/human/build.log 2>&1
