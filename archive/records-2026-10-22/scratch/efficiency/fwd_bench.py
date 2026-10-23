"""Records forward_rows batches from a real 64-game self-play shard (round_066,
production settings) then times GraphNet.forward_rows against a candidate
graph.forward_np and checks bitwise equality.

usage: fwd_bench.py <calls to record> [record_path]
"""

import os
import pickle
import sys
import timeit

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

from catanatron.colonist import graph as G
from catanatron.colonist.net import Net

want = int(sys.argv[1])
path = (
    sys.argv[2]
    if len(sys.argv) > 2
    else "data/orchestrator/scratch/efficiency/fwd_batches.pkl"
)


class Enough(Exception):
    pass


if os.path.exists(path):
    with open(path, "rb") as f:
        batches = pickle.load(f)
else:
    from catanatron.colonist import selfplay as S

    batches = []
    orig = G.GraphNet.forward_rows

    def recording(self, statics, dynamic):
        if len(dynamic) > 8:
            batches.append((list(statics), dynamic.copy()))
        if len(batches) >= want:
            raise Enough
        return orig(self, statics, dynamic)

    G.GraphNet.forward_rows = recording
    nets = "runs/g1/nets"
    task = (
        "data/orchestrator/scratch/efficiency/fwd_out",
        f"{nets}/round_066.npz",
        9_100_000,
        64,
        {
            "simulations": 400,
            "setup_noise_scale": 1.0,
            "full_rate": 0.25,
            "fast_simulations": 24,
        },
    )
    try:
        S.run_shard(task)
    except Enough:
        pass
    G.GraphNet.forward_rows = orig
    with open(path, "wb") as f:
        pickle.dump(batches, f)

net = Net.load("runs/g1/nets/round_066.npz")
sizes = [len(d) for _, d in batches]
print(len(batches), "batches; rows mean", np.mean(sizes), "max", max(sizes))

new = getattr(G, "forward_np", None)
if new is not None:
    differ = 0
    dl = dv = 0.0
    flips = rows = 0
    for s, d in batches:
        a = net.forward_rows(s, d)
        b = new(net.params, s, d, net.layers)
        if a[0].tobytes() != b[0].tobytes() or a[1].tobytes() != b[1].tobytes():
            differ += 1
        dl = max(dl, float(np.abs(a[0] - b[0]).max()))
        dv = max(dv, float(np.abs(a[1] - b[1]).max()))
        flips += int((a[0].argmax(1) != b[0].argmax(1)).sum())
        rows += len(d)
    print(
        "candidate differs bitwise on",
        differ,
        "of",
        len(batches),
        f"batches; max |dlogit| {dl:.2e}, max |dvalue| {dv:.2e}; "
        f"argmax over all 313 differs on {flips} of {rows} rows",
    )

fns = [("old", lambda s, d: net.forward_rows(s, d))]
if new is not None:
    fns.append(("new", lambda s, d: new(net.params, s, d, net.layers)))
for name, fn in fns:
    t = min(timeit.repeat(lambda: [fn(s, d) for s, d in batches], number=1, repeat=3))
    print(
        f"{name}: {t / len(batches) * 1e6:.1f} us per call, "
        f"{t / sum(sizes) * 1e6:.2f} us per row"
    )
