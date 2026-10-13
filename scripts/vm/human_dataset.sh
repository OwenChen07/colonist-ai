#!/usr/bin/env bash
# Before every training round (train.sh's --before-round) and weekly (cron on
# the coordinator): add new games against people to the human-game training
# set, data/human (human_dataset.py; it skips captures it has already indexed).
# train.sh reads data/human/train/value each round, and self-play starts a
# share of its games from data/human/starts.jsonl.gz (starts.py, rebuilt here
# from the training split). Then, in the background, the human opponent model
# is retrained from the best net and replaced if it predicts held-out people
# at least as well (opponent_model.py refresh); sync_bot.sh copies it to the
# bot VMs, whose runners reload it between games.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec 9>/tmp/human_dataset.lock
flock 9  # the weekly cron run and a round's run never overlap
nice -n 19 .venv/bin/python -m catanatron.colonist.human_dataset \
    "captures/*/bridge-*.json" --games "data/*/games.jsonl" --out data/human \
    --own-accounts CatBot77,Ericha8499,Dilly6283,Sherie3424,Petra4620,hersh3499 \
    >> data/human/build.log 2>&1
nice -n 19 .venv/bin/python -m catanatron.colonist.starts data/human \
    --out data/human/starts.jsonl.gz >> data/human/build.log 2>&1
mkdir -p models/human
nohup flock -n /tmp/opponent_model.lock env OMP_NUM_THREADS=3 nice -n 15 \
    .venv/bin/python -m catanatron.colonist.opponent_model refresh \
    data/human/train/opponent --holdout data/human/holdout/opponent \
    --init runs/g1/best.npz --model models/human/net.npz -- --device cpu --epochs 10 \
    >> models/human/refresh.log 2>&1 < /dev/null 9>&- &
