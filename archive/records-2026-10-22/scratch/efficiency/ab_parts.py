"""Plays seeds first..first+total-1 as consecutive parts of `part` games in one
process (production settings, round 73) and prints the wall time.

usage: ab_parts.py <out_dir> <first> <total> <part>
"""

import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

from catanatron.colonist.selfplay import run_shard

out_dir, first, total, part = sys.argv[1], *map(int, sys.argv[2:5])
nets = "runs/g1/nets"
t0 = time.perf_counter()
rows = calls = 0
for f in range(first, first + total, part):
    task = (
        out_dir,
        f"{nets}/round_073.npz",
        f,
        min(part, first + total - f),
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
    path, count, r, searches, c, secs = run_shard(task)
    rows += r
    calls += c
    print(f"part {f} x{count}: {secs:.1f} s, {r} rows, {c} calls", flush=True)
wall = time.perf_counter() - t0
print(
    f"PART {part}: {total} games {wall:.1f} s = {wall / total:.2f} s/game; "
    f"{rows} rows, {calls} calls",
    flush=True,
)
