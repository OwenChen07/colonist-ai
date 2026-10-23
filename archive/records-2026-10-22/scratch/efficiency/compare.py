"""Which arrays differ between the queue's shards and the whole run's, and by how much."""

import os
import sys

import numpy as np

BASE = "data/orchestrator/scratch/efficiency/e2e"
for name in sorted(os.listdir(os.path.join(BASE, "ref"))):
    if not name.startswith("shard_"):
        continue
    with (
        np.load(os.path.join(BASE, "run/round/data", name)) as a,
        np.load(os.path.join(BASE, "ref", name)) as b,
    ):
        print(name, "rows", len(a["game"]), len(b["game"]))
        for k in a.files:
            x, y = a[k], b[k]
            if x.shape != y.shape:
                print(f"  {k}: shape {x.shape} vs {y.shape}")
            elif not np.array_equal(x, y):
                d = np.abs(x.astype(np.float64) - y.astype(np.float64))
                rows = np.unique(np.nonzero(d.reshape(len(d), -1).max(1))[0])
                print(
                    f"  {k}: max diff {d.max():.2e}, {len(rows)} rows differ "
                    f"of {len(d)}, games {sorted(set(a['game'][rows].tolist()))[:10] if k != 'static' else ''}"
                )
