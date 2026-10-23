"""forward_np microseconds per row against batch size, from the recorded
real batches (fwd_batches.pkl). usage: fwd_scale.py [net]"""

import os
import pickle
import sys
import timeit

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

from catanatron.colonist import graph as G
from catanatron.colonist.net import Net

net = Net.load(sys.argv[1] if len(sys.argv) > 1 else "runs/g1/nets/round_066.npz")
print(
    "layers",
    net.layers,
    {
        k: v[0].shape
        for k, v in net.params.items()
        if k.startswith(("node0", "glob0", "node_in"))
    },
)
with open("data/orchestrator/scratch/efficiency/fwd_batches.pkl", "rb") as f:
    batches = pickle.load(f)
statics = [s for st, _ in batches for s in st]
dynamic = np.concatenate([d for _, d in batches])
print("rows recorded", len(statics), "mean batch", len(statics) / len(batches))
for size in (1, 4, 8, 16, 32, 64, 128, 256):
    calls = [
        (statics[i : i + size], dynamic[i : i + size])
        for i in range(0, min(len(statics), 8192) - size + 1, size)
    ]
    rows = sum(len(d) for _, d in calls)
    t = min(
        timeit.repeat(
            lambda: [G.forward_np(net.params, s, d, net.layers) for s, d in calls],
            number=1,
            repeat=3,
        )
    )
    print(
        f"batch {size:4d}: {t / len(calls) * 1e6:8.1f} us/call {t / rows * 1e6:7.2f} us/row"
    )
