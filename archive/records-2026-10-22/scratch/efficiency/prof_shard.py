"""Profile one self-play shard in-process with production search settings.

usage: prof_shard.py <out_dir> <games> <prof_file>
"""

import cProfile
import os
import pstats
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from catanatron.colonist.selfplay import run_shard

out_dir, games, prof_file = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.makedirs(out_dir, exist_ok=True)
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
profiler = cProfile.Profile()
profiler.enable()
result = run_shard(task)
profiler.disable()
profiler.dump_stats(prof_file)
print("result", result[1:], flush=True)
stats = pstats.Stats(prof_file)
stats.sort_stats("tottime").print_stats(35)
stats.sort_stats("cumulative").print_stats(45)
