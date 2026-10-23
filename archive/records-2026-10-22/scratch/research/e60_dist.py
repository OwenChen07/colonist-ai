"""E60 phase 0: an exploiter of round 73 (offline, a diagnostic).

    .venv/bin/python data/orchestrator/scratch/research/e60_dist.py OUT \
        --vm owen@35.231.142.146 --per-host 8 [--iters 10] [--games 20000] \
        [--eval-every 2] [--eval-games 1000] [--seed 6000000000]

e54_dist.py's loop with one change: the learner plays round 73 in every game
(pool = round 73 only, no self-play, no people model), so it learns a best
response to round 73 rather than a general improvement. Starts from round 73;
policy-gradient updates (train.py --pg, KL to round 73) on the GPU host; raw vs
raw against round 73 every --eval-every iterations in OUT/eval.txt. In --vm
mode one iteration per promotion match under the VM's gpu.lock, as E54.

The design's 400-simulation self-play does not fit a 15-minute slot on 8
cores (about 11 s a game a core: 3,500 games is about 80 min), so phase 0
uses E54's search-free games (0.2 s a game). A raw-net hole may be one the
search covers, so a pass here (55%+ raw vs raw) is confirmed with the
200-simulation gate.py match on the fleet (2,000 games, the lead's time).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e54_dist  # noqa: E402
from e54_loop import R73  # noqa: E402

e54_dist.NAME = "e60"
e54_dist.REMOTE = "/home/owen/e60"
e54_dist.POOL = [R73]
e54_dist.HUMAN = None
e54_dist.SELF_RATE = 0.0

if __name__ == "__main__":
    e54_dist.main()
