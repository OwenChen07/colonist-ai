"""Positions per shard folder (and turn>=75 share): count_rows.py FOLDER..."""

import glob
import os
import sys

import numpy as np

for d in sys.argv[1:]:
    n = late = 0
    files = glob.glob(os.path.join(d, "shard_*.npz"))
    for p in files:
        with np.load(p) as s:
            n += len(s["game"])
            if "turn" in s.files:
                late += int((s["turn"] >= 75).sum())
    print(f"{d}: {len(files)} shards, {n} positions, turn>=75 {late}")
