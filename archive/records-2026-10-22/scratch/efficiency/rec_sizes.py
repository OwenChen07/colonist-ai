"""Plays a self-play part (production settings) recording every forward call's
batch size and time, and a sample of the batches.

usage: rec_sizes.py <out_dir> <games> <pickle>
"""

import collections
import os
import pickle
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from catanatron.colonist import graph as G
from catanatron.colonist.selfplay import run_shard

out_dir, games, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.makedirs(out_dir, exist_ok=True)
sizes, sample = [], []
orig = G.GraphNet.forward_rows


def recording(self, statics, dynamic):
    t = time.perf_counter()
    r = orig(self, statics, dynamic)
    sizes.append((len(dynamic), time.perf_counter() - t))
    if len(sizes) % 7 == 0:
        sample.append((list(statics), dynamic.copy()))
    return r


G.GraphNet.forward_rows = recording
nets = "runs/g1/nets"
task = (
    out_dir,
    f"{nets}/round_073.npz",
    7_000_000,
    games,
    {
        "simulations": 400,
        "setup_noise_scale": 1.0,
        "full_rate": 0.25,
        "fast_simulations": 24,
    },
    (f"{nets}/round_070.npz", f"{nets}/round_071.npz", "AB:2", "F"),
    0.2,
    None,
    0.0,
    None,
    0.0,
)
t0 = time.perf_counter()
run_shard(task)
total = time.perf_counter() - t0
with open(path, "wb") as f:
    pickle.dump({"sizes": sizes, "sample": sample}, f)
by = collections.defaultdict(lambda: [0, 0.0])
for n, t in sizes:
    k = n if n <= 16 else (32 if n <= 32 else (64 if n <= 64 else 999))
    by[k][0] += 1
    by[k][1] += t
fwd = sum(t for _, t in sizes)
print(
    f"{games} games {total:.1f} s; {len(sizes)} calls, "
    f"{sum(n for n, _ in sizes)} rows, forward {fwd:.1f} s ({fwd / total:.0%})"
)
for k in sorted(by):
    c, t = by[k]
    print(
        f"  size {'<=' if k > 16 else '  '}{k:4d}: {c:6d} calls, {t:6.2f} s "
        f"({t / fwd:.0%} of forward), {t / c * 1e6:7.0f} us/call"
    )
