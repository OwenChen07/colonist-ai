"""E42: time one training round on the trial project's GPU VM with round 102's exact inputs.

.venv/bin/python data/orchestrator/scratch/efficiency/gpu_bench.py owen@<ip> [epochs]
"""

import sys
import time
from types import SimpleNamespace

from catanatron.colonist import loop

host = sys.argv[1]
epochs = sys.argv[2] if len(sys.argv) > 2 else "1"
data = [f"runs/g1/round_{k:03d}/data" for k in range(97, 103)]
data += ["data/human/train/value"] * 4 + ["data/human/train/policy_won"] * 3
out = "data/orchestrator/scratch/efficiency/gpu_bench_out"
init = "models/graph1/net.npz"
argv = [
    *data,
    "--out",
    out,
    "--init",
    init,
    "--epochs",
    epochs,
    "--lr",
    "0.0003",
    "--sample",
    "4000000",
    "--ema",
    "0.9997",
]
args = SimpleNamespace(
    train_host=host, train_device="cuda", train_max_positions=4_000_000
)
t = time.time()
loop.train_remote(args, argv, data, out, init, 102)
print(f"TOTAL {time.time() - t:.0f} s", flush=True)
