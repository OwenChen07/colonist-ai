"""Times graph.forward_np against fwd2_cand.forward_np on recorded batches cut
to given sizes, and checks bitwise equality.

usage: fwd2.py [net] [sizes...]
"""

import os
import pickle
import sys
import timeit

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

for a in [a for a in sys.argv if "=" in a]:  # KEY=value sets the environment
    sys.argv.remove(a)
    os.environ[a.split("=")[0]] = a.split("=", 1)[1]
sys.path.insert(0, os.path.dirname(__file__))
from catanatron.colonist import graph as G
from catanatron.colonist.net import Net
import fwd2_cand as C

net_path = sys.argv[1] if len(sys.argv) > 1 else "runs/g1/nets/round_073.npz"
sizes = [int(a) for a in sys.argv[2:]] or [1, 4, 8, 16, 64, 256]
with open("data/orchestrator/scratch/efficiency/fwd_batches.pkl", "rb") as f:
    batches = pickle.load(f)
net = Net.load(net_path)
print(
    {k: w.shape for k, (w, b) in net.params.items() if k in ("node0", "glob0")},
    "layers",
    net.layers,
)
rows_s = [s for ss, _ in batches for s in ss]
rows_d = np.concatenate([d for _, d in batches])

real = os.environ.get("REAL")  # a rec_sizes.py pickle: its sampled calls
if real:
    with open(real, "rb") as f:
        sample = pickle.load(f)["sample"]
    sizes = [0]
for size in sizes:
    if real:
        calls = sample
    else:
        calls = []
        for i in range(0, min(len(rows_d), 20000) - size + 1, size):
            calls.append((rows_s[i : i + size], rows_d[i : i + size]))
        calls = calls[: max(50, 4000 // size)]
    differ, dl = 0, 0.0
    for s, d in calls:
        a = G.forward_np(net.params, s, d, net.layers)
        b = C.forward_np(net.params, s, d, net.layers)
        if a[0].tobytes() != b[0].tobytes() or a[1].tobytes() != b[1].tobytes():
            differ += 1
            dl = max(
                dl, float(np.abs(a[0] - b[0]).max()), float(np.abs(a[1] - b[1]).max())
            )
    best = [1e9, 1e9]
    for _ in range(int(os.environ.get("ROUNDS", "7"))):
        for i, fn in enumerate((G.forward_np, C.forward_np)):
            t = timeit.timeit(
                lambda: [fn(net.params, s, d, net.layers) for s, d in calls], number=1
            )
            best[i] = min(best[i], t)
    out = [t / len(calls) * 1e6 for t in best]
    print(
        f"batch {size:4d}: old {out[0]:8.1f} us/call, new {out[1]:8.1f} "
        f"({out[1] / out[0]:.3f}); differ {differ}/{len(calls)} max {dl:.1e}"
    )
