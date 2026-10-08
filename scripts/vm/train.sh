#!/usr/bin/env bash
# Step 2 of the plateau plan: KataGo-style playout cap randomization, from
# round 8's net (runs/sp1 resumes at round 11). Every new net is also scored
# on the live ranked games and, when promoted, against earlier rounds.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec .venv/bin/python -m catanatron.colonist.loop runs/sp1 \
    --rounds 1000 --games 5000 \
    --simulations 160 --full-rate 0.25 --fast-simulations 24 \
    --reference AB:2,F --extra-data data/ab2 \
    --gauntlet runs/sp1/nets/round_002.npz,runs/sp1/nets/round_005.npz \
    --live-data data/ranked/bot_value \
    --workers "${COLONIST_LOCAL_WORKERS:-7}"
