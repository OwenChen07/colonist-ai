#!/usr/bin/env bash
# Run on the PC after verified test shards have been copied there.
set -euo pipefail
cd /home/owen/colonist-ai

root=experiments/search-mix-20261015
base=experiments/epoch4-r43
test "$(python3 -c "import json;print(json.load(open('$root/data-verified.json'))['status'])")" = verified
test "$(sha256sum "$base/init-round40.npz" | cut -d' ' -f1)" = c3febf660ac176393e8ecc9bab3c8dfa5bcd13168679832081c8976d2f252172
test ! -e "$root/train.finished_at"
test ! -e "$root/train/log.csv"

# Share the same GPU lock as the production trainer and earlier experiments.
( set -C; : > gpu.lock ) || { echo 'GPU is reserved' >&2; exit 1; }
trap 'rm -f gpu.lock' EXIT
date -u +%FT%TZ > "$root/train.started_at"
timeout 40m env OMP_NUM_THREADS=8 OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python -m catanatron.colonist.train \
  "$base/runs/g1/round_038/data" "$base/runs/g1/round_039/data" \
  "$base/runs/g1/round_040/data" "$base/runs/g1/round_041/data" \
  "$base/runs/g1/round_042/data" "$root/data" \
  "$base/data/human/train/value" "$base/data/human/train/value" \
  "$base/data/human/train/value" "$base/data/human/train/value" \
  --out "$root/train" --init "$base/init-round40.npz" \
  --epochs 3 --lr 0.0003 --sample 4000000 --device cuda \
  --data-device cpu --max-positions 4000000 --seed 43 \
  > "$root/train.stdout.log" 2>&1
date -u +%FT%TZ > "$root/train.finished_at"
