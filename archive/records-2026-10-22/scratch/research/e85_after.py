"""E85's checks, unattended, once data/orchestrator/e85/net.npz exists:
(1) the people holdout, paired against step 50 and round 189 (holdout.txt);
(2) top-move agreement with step 50 on people's positions (policy_won holdout)
and on self-play positions from round 199 (agree.txt). Pass needs >= 95%.

    .venv/bin/python data/orchestrator/scratch/research/e85_after.py
"""

import glob
import os
import subprocess
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e85"
PY = f"{ROOT}/.venv/bin/python"
R = f"{ROOT}/data/orchestrator/scratch/research"
STEP50 = f"{ROOT}/data/orchestrator/e54/run1/iter-50/net.npz"


def top_moves(net, d):
    static, dyn, game = d["static"], d["dynamic"].astype(np.float32), d["game"]
    logits, _ = net.forward_rows([static[g].astype(np.float32) for g in game], dyn)
    legal = np.unpackbits(d["legal"], axis=1, count=E.NUM_ACTIONS).astype(bool)
    return np.where(legal, logits, -np.inf).argmax(axis=1)


def agree(a, b, paths, label, f):
    same = total = 0
    for p in paths:
        with np.load(p) as d:
            if "legal" not in d:
                continue
            x, y = top_moves(a, d), top_moves(b, d)
        same += int((x == y).sum())
        total += len(x)
    pct = 100.0 * same / max(total, 1)
    print(
        f"{label}: top move same as step 50 on {pct:.1f}% of {total} positions "
        f"({len(paths)} shards)",
        file=f,
        flush=True,
    )


def main():
    net_path = f"{OUT}/net.npz"
    while not os.path.exists(net_path):
        time.sleep(60)
    time.sleep(30)  # let the copy finish
    print("net.npz found; starting checks", flush=True)
    with open(f"{OUT}/holdout.txt", "w") as f:
        hold = subprocess.Popen(
            [PY, f"{R}/e54_holdout.py", f"e85={net_path}", f"i50={STEP50}"],
            cwd=ROOT,
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    a, b = Net.load(net_path), Net.load(STEP50)
    people = sorted(glob.glob(f"{ROOT}/data/human/holdout/policy_won/shard_*.npz"))
    selfplay = sorted(glob.glob(f"{ROOT}/runs/g1/round_198/data/shard_*.npz"))[:40]
    with open(f"{OUT}/agree.txt", "w") as f:
        agree(a, b, people, "people (holdout)", f)
        agree(a, b, selfplay, "self-play (round 198)", f)
    hold.wait()
    print("done", flush=True)


if __name__ == "__main__":
    main()
