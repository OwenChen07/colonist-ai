"""E54 nets against rounds 73 and 152 on the human holdout, by opponent rating band:
value MSE by phase, paired game bootstrap of MSE differences against round 152 and 73.
Adapted from scratch/training/human2100.py.

    .venv/bin/python data/orchestrator/scratch/research/e54_holdout.py LABEL=NET ...
"""

import glob
import json
import os
import re
import sys

import numpy as np

from catanatron.colonist.calibrate import calibrate, describe
from catanatron.colonist.net import Net

H = "/home/owen/colonist-ai/data/human/holdout"
RATINGS = json.load(open("/home/owen/colonist-ai/data/orchestrator/e66/ratings.json"))
BASE = {
    "r73": "/home/owen/colonist-ai/runs/g1/round_073/train/net.npz",
    "r152": "/home/owen/colonist-ai/runs/g1/round_152/train/net.npz",
    "r189": "/home/owen/colonist-ai/runs/g1/round_189/train/net.npz",
}
# E83's rule compares later steps with step 30 (pass it as i30=PATH).
REF = ("r152", "r189", "r73", "i30", "i50")
BANDS = [
    ("2100-2300", 2100, 2300),
    ("2300+", 2300, 9999),
    ("2100+", 2100, 9999),
    ("under 2100", 0, 2100),
    ("all rated", 0, 9999),
]


def mmr(path):
    m = re.search(r"shard_(\d+)_", os.path.basename(path))
    r = RATINGS.get(m.group(1)) if m else None
    return r["mmr"] if r and r.get("mmr") else None


def load(paths):
    parts = []
    for p in paths:
        with np.load(p) as d:
            parts.append(
                (
                    d["static"].astype(np.float32),
                    d["dynamic"].astype(np.float32),
                    d["game"].astype(np.int64),
                    d["z"].astype(np.float32),
                    d["turn"].astype(np.int64),
                )
            )
    return parts


def per_game_sq(net, parts):
    out = []
    for static, dyn, game, z, _ in parts:
        _, v = net.forward_rows([static[g] for g in game], dyn)
        e = (np.asarray(v, np.float64) - z.astype(np.float64)) ** 2
        out.append((e.sum(), len(e)))
    return np.array(out)


def main():
    paths = dict(BASE)
    for a in sys.argv[1:]:
        k, v = a.split("=", 1)
        paths[k] = v
    nets = {k: Net.load(p) for k, p in paths.items()}
    vpaths = sorted(glob.glob(f"{H}/value/shard_*.npz"))
    rng = np.random.default_rng(0)
    for band, lo, hi in BANDS:
        vp = [p for p in vpaths if mmr(p) is not None and lo <= mmr(p) < hi]
        parts = load(vp)
        print(f"\n== {band}: {len(vp)} games", flush=True)
        sq = {}
        for k, net in nets.items():
            print(f"{k}: {describe(calibrate(net, parts))}", flush=True)
            sq[k] = per_game_sq(net, parts)
        n = len(vp)
        idx = [rng.integers(0, n, n) for _ in range(2000)]
        for k in nets:
            for b in REF:
                if k in BASE or k == b or b not in nets:
                    continue
                mse = lambda s, i: s[i, 0].sum() / s[i, 1].sum()
                d = [mse(sq[k], i) - mse(sq[b], i) for i in idx]
                lo_, hi_ = np.percentile(d, [2.5, 97.5])
                d0 = mse(sq[k], slice(None)) - mse(sq[b], slice(None))
                print(
                    f"  mse {k} - {b}: {d0:+.4f} (95% {lo_:+.4f} to {hi_:+.4f})",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
