#!/usr/bin/env bash
# The graph net's training run (runs/g1), from models/graph1 (graph.py, trained
# from scratch on runs/sp1's rounds 9-14; it beat sp1's round 15 at 57%).
# For its first rounds, sp1's last self-play (rounds 13-16, played by the MLP
# nets) is mixed in, so training doesn't start from one round of its own.
# Same self-play, gate and evaluation as train-sp1.sh; the gauntlet anchors
# progress against the old line (sp1 round 16 and round 8).
set -euo pipefail
cd "$(dirname "$0")/../.."
init=()
[ -e runs/g1/state.json ] || init=(--init models/graph1/net.npz)
sp1=runs/sp1
exec .venv/bin/python -m catanatron.colonist.loop runs/g1 "${init[@]}" \
    --rounds 1000 --games 5000 --window 8 --train-sample 4000000 --gate loose \
    --distribute-eval \
    --simulations 160 --full-rate 0.25 --fast-simulations 24 \
    --reference AB:2,F \
    --extra-data $sp1/round_013/data,$sp1/round_014/data,$sp1/round_015/data,$sp1/round_016/data \
    --gauntlet $sp1/nets/round_016.npz,$sp1/nets/round_008.npz \
    --live-data data/ranked/bot_value \
    --workers "${COLONIST_LOCAL_WORKERS:-7}"
