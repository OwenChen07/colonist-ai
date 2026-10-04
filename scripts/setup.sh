#!/usr/bin/env bash
# One-command setup on a fresh machine (the MacBook, or a rented Linux VM):
#
#   git clone <this repo> colonist-ai && cd colonist-ai && scripts/setup.sh
#
# Makes .venv, installs the simulator with the colonist and training extras,
# runs the colonist tests, and prints the machine's core count. Safe to rerun.
# Then, for example:
#
#   .venv/bin/python -m catanatron.colonist.datagen data/ab2 --games 40000
#   .venv/bin/python -m catanatron.colonist.train data/ab2 --out models/ab2
#   .venv/bin/python bench/selfplay.py models/ab2/net.npz
#
# Long runs resume where they stopped when rerun with the same arguments, so
# run them under nohup or tmux on a VM and copy data/ and models/ back.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "needs Python 3.11 or newer (set PYTHON=...)" >&2
  exit 1
fi
[ -d .venv ] || "$PYTHON" -m venv .venv
PIP=".venv/bin/python -m pip"
$PIP install -q --upgrade pip

# On a Linux machine without an NVIDIA GPU, the CPU build of PyTorch is a
# fraction of the size of the default one (which bundles CUDA).
if [ "$(uname)" = "Linux" ] && ! command -v nvidia-smi >/dev/null 2>&1; then
  $PIP install -q torch --index-url https://download.pytorch.org/whl/cpu \
    || echo "CPU PyTorch index unreachable; falling back to the default build"
fi
$PIP install -q -e ".[colonist,train,dev]"

.venv/bin/python -m pytest tests/colonist -q
CORES=$(.venv/bin/python -c 'import os; print(os.cpu_count())')
echo "Ready: $CORES cores. Data and evaluation runs use all of them by default (--workers)."
