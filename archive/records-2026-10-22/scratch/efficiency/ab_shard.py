"""Play one shard with production settings; print time and a hash of its data,
overall and per key.

usage: ab_shard.py old|new <out_dir> <games> <seed>
'old' patches GraphNet.forward_rows back to the dense forward.
"""

import hashlib
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

from catanatron.colonist import graph as G
from catanatron.colonist.selfplay import run_shard

arm, out_dir, games, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
if arm == "old":

    def old_forward_rows(self, statics, dynamic):
        x = np.concatenate([np.stack(statics), dynamic], 1).astype(np.float32)
        return G.forward(self.params, x, self.layers)

    G.GraphNet.forward_rows = old_forward_rows
os.makedirs(out_dir, exist_ok=True)
nets = "runs/g1/nets"
task = (
    out_dir,
    f"{nets}/round_066.npz",
    seed,
    games,
    {
        "simulations": 400,
        "setup_noise_scale": 1.0,
        "full_rate": 0.25,
        "fast_simulations": 24,
    },
    (f"{nets}/round_063.npz", f"{nets}/round_064.npz", "AB:2", "F"),
    0.2,
    None,
    0.0,
    None,
    0.0,
)
start = time.time()
path, count, rows, searches, calls, secs = run_shard(task)
wall = time.time() - start
digest = hashlib.sha1()
per_key = []
with np.load(path) as data:
    for key in sorted(data.files):
        raw = np.ascontiguousarray(data[key]).tobytes()
        digest.update(key.encode())
        digest.update(raw)
        per_key.append(f"{key}={hashlib.sha1(raw).hexdigest()[:8]}")
print(
    f"{arm}: {count} games, {rows} rows, {searches} searches, {calls} calls, "
    f"{wall:.1f} s ({wall / count:.2f} s/game); data sha1 {digest.hexdigest()[:16]}"
)
print(" ".join(per_key))
