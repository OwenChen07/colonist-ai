"""E84 step 2 data: each E84 late game's start row (its first row), one shard
per E84 shard in data/orchestrator/e84/starts/, and the repeat count K that
makes them about 10% of the positions train.py draws.

    .venv/bin/python data/orchestrator/scratch/research/e84_starts2.py
"""

import glob
import os

import numpy as np

D = "data/orchestrator/e84"
OUT = f"{D}/starts"
ROUNDS = range(182, 200)
SHARE = 0.10


def rows(path):
    with np.load(path) as s:
        return len(s["game"])


def main():
    os.makedirs(OUT, exist_ok=True)
    n_start = n_late = 0
    for f in sorted(glob.glob(f"{D}/data/shard_*.npz")):
        with np.load(f) as s:
            d = {k: s[k] for k in s.files}
        n = len(d["game"])
        n_late += n
        game = d["game"]
        first = np.flatnonzero(np.r_[True, game[1:] != game[:-1]])
        assert len(np.unique(game[first])) == len(first)
        keep = {k: (v[first] if v.ndim and len(v) == n else v) for k, v in d.items()}
        np.savez_compressed(os.path.join(OUT, os.path.basename(f)), **keep)
        n_start += len(first)
    pool = n_late
    for r in ROUNDS:
        pool += sum(
            rows(p) for p in glob.glob(f"runs/g1/round_{r:03d}/data/shard_*.npz")
        )
    human = sum(rows(p) for p in glob.glob("data/human/train/value/shard_*.npz"))
    pool += 12 * human
    k = round(SHARE * pool / ((1 - SHARE) * n_start))
    print(f"start rows {n_start}, late rows {n_late}, human value rows {human} (x12)")
    print(
        f"pool without starts {pool}; K {k} -> starts share "
        f"{k * n_start / (pool + k * n_start):.3f}, each start row drawn about "
        f"{4e6 / (pool + k * n_start) * k:.1f} times per epoch"
    )


if __name__ == "__main__":
    main()
