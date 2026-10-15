#!/usr/bin/env bash
# Isolated 25%/400 versus 50%/212 self-play search-mix comparison.
set -euo pipefail
cd "$(dirname "$0")/../.."

root=data/experiments/search-mix-20261015
input=data/experiments/human-start-rate-20261014/input
mkdir -p "$root/data"
test "$(shasum -a 256 "$input/best.npz" | cut -d' ' -f1)" = c3febf660ac176393e8ecc9bab3c8dfa5bcd13168679832081c8976d2f252172
test "$(shasum -a 256 "$input/starts.jsonl.gz" | cut -d' ' -f1)" = dd9edf10ccd60454b36f0139df2a030076d14aabc7bb7d1885396484c23a05f3

# Keep setup at 400 in both branches. For other moves, both mixes average
# 118 simulations: .25*400 + .75*24 = .50*212 + .50*24.
# The frozen E7 data35 branch is the control; reruns skip completed shards.
opponents="$input/round_037.npz,$input/round_038.npz,$input/round_039.npz,AB:2,F"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
.venv/bin/python -m catanatron.colonist.selfplay \
  "$input/best.npz" "$root/data" \
  --games 3500 --seed 4043000000 --shard-size 16 --workers 4 \
  --simulations 212 --setup-simulations 400 \
  --full-rate 0.5 --fast-simulations 24 \
  --opponents "$opponents" --opponent-rate 0.2 \
  --human-starts "$input/starts.jsonl.gz" --human-start-rate 0.35
