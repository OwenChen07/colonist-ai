"""Seconds per game of one production self-play shard, plain or from a Cython
build (no timer wrappers, so the two are comparable).

usage: cy_time.py <out_dir> <games> [build_dir]
"""

import os
import sys
import time

if len(sys.argv) > 3:
    sys.path.insert(0, sys.argv[3])
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from catanatron import game as _game  # noqa: E402
from catanatron.colonist import selfplay as S  # noqa: E402

print("engine from", _game.__file__)
out_dir, games = sys.argv[1], int(sys.argv[2])
nets = "runs/g1/nets"
task = (
    out_dir,
    f"{nets}/round_066.npz",
    7_000_000,
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
)
os.makedirs(out_dir, exist_ok=True)
t0 = time.perf_counter()
result = S.run_shard(task)
wall = time.perf_counter() - t0
print("result", result[1:], f"wall {wall:.1f}s, {wall / games:.2f} s/game")
