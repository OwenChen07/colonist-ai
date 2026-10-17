#!/usr/bin/env bash
# Lands a workstream's commits onto main-9mnmbh the owner's way, from the
# worker's worktree: rebase on the latest main-9mnmbh, black, the full tests,
# push to main-9mnmbh and main, then bring the coordinator's checkout
# (~/colonist-ai, which training and deploys run from) up to date. One landing
# at a time across workers.
#   scripts/vm/orchestrator/land.sh
set -euo pipefail
wt=$(git rev-parse --show-toplevel)
main=$HOME/colonist-ai
[ "$wt" != "$(cd "$main" && pwd -P)" ] || { echo "run this from a workstream worktree"; exit 1; }
[ -z "$(git -C "$wt" status --porcelain)" ] || { echo "commit or discard your changes first"; git -C "$wt" status --short; exit 1; }
mkdir -p "$main/data/orchestrator"
exec 8>"$main/data/orchestrator/.land.lock"
flock 8
cd "$wt"
git fetch -q origin main-9mnmbh
if [ -z "$(git rev-list origin/main-9mnmbh..HEAD)" ]; then echo "nothing to land"; exit 0; fi
git rebase origin/main-9mnmbh || { git rebase --abort; echo "rebase conflict: resolve it on your branch, then land again"; exit 1; }
.venv/bin/python -m black -q .
if [ -n "$(git status --porcelain)" ]; then
  echo "black reformatted files; commit them and land again"; git status --short; exit 1
fi
if ! out=$(PYTHONPATH="$wt/catanatron:$wt" .venv/bin/python -m pytest -q -x -p no:cacheprovider 2>&1); then
  echo "$out" | tail -30; echo "tests failed: not landed"; exit 1
fi
echo "$out" | tail -2
git push -q origin HEAD:main-9mnmbh
git push -q origin HEAD:main
git -C "$main" pull -q --rebase --autostash origin main-9mnmbh
echo "landed $(git log --oneline -1); ~/colonist-ai is at $(git -C "$main" log --oneline -1)"
