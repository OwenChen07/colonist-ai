"""Score the live-loss robber failure set across training rounds (prior, then search)."""

import os
import sys

sys.path.insert(
    0, "/home/owen/colonist-ai-wt/training/data/orchestrator/scratch/live-loss"
)
import robber_set  # noqa: E402

NET = "/home/owen/colonist-ai/runs/g1/round_{:03d}/train/net.npz"
PRIOR = (13, 40, 73, 110, 152, 168, 182)
SEARCH = (13, 73, 152)

for r in PRIOR:
    p = NET.format(r)
    if os.path.exists(p):
        robber_set.score(p, 0, 3)
        sys.stdout.flush()
for r in SEARCH:
    robber_set.score(NET.format(r), 200, 3)
    sys.stdout.flush()
