#!/usr/bin/env bash
# Step 2 of the plateau plan: KataGo-style playout cap randomization, from
# round 8's net (runs/sp1 resumes at round 11). Every new net is also scored
# on the live ranked games and, when promoted, against earlier rounds.
# Training draws 4M positions per epoch from the last 8 rounds (about 9.5 GB
# in memory with the AlphaBeta data), the same time per round as 3 rounds.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m catanatron.colonist.loop runs/sp1 \
    --rounds 1000 --games 5000 --window 8 --train-sample 4000000 --gate loose \
    --simulations 160 --full-rate 0.25 --fast-simulations 24 \
    --reference AB:2,F --extra-data data/ab2 \
    --gauntlet runs/sp1/nets/round_002.npz,runs/sp1/nets/round_005.npz \
    --live-data data/ranked/bot_value \
    --workers "${COLONIST_LOCAL_WORKERS:-7}"
