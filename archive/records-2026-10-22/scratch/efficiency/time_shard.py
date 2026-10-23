"""Wall-time shares of one self-play shard (production settings), by wrapping
leaf components in perf_counter timers (no profiler overhead elsewhere).

usage: time_shard.py <out_dir> <games>
"""

import collections
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist import graph as G
from catanatron.colonist import mcts as M
from catanatron.colonist import selfplay as S
from catanatron.game import Game

spent = collections.Counter()
calls = collections.Counter()
pc = time.perf_counter


def wrap(owner, attr, label):
    orig = getattr(owner, attr)

    def timed(*a, **k):
        t = pc()
        try:
            return orig(*a, **k)
        finally:
            spent[label] += pc() - t
            calls[label] += 1

    setattr(owner, attr, timed)


wrap(G.GraphNet, "forward_rows", "net forward_rows")
wrap(E, "encode", "encode")
wrap(E, "legal_indices", "legal_indices")
wrap(Game, "copy", "game.copy")
wrap(M.Decision, "set_evaluation", "set_evaluation")
wrap(M.Search, "pick", "mcts pick")
wrap(M.Search, "backup", "mcts backup")
wrap(M, "outcomes", "chance outcomes")
wrap(M.Search, "node_after", "node_after (execute forced)")
wrap(Game, "execute", "game.execute (all)")
wrap(np, "stack", "np.stack")

import gc

gc_start = {}
gc_spent = collections.Counter()
gc_count = collections.Counter()


def on_gc(phase, info):
    if phase == "start":
        gc_start["t"] = pc()
    else:
        gc_spent[info["generation"]] += pc() - gc_start["t"]
        gc_count[info["generation"]] += 1


gc.callbacks.append(on_gc)
if len(sys.argv) > 3 and sys.argv[3] == "old":
    gc.disable = lambda: None  # selfplay's play_games leaves the GC on
elif len(sys.argv) > 3:
    gc.set_threshold(*[int(x) for x in sys.argv[3].split(",")])
print("gc threshold", gc.get_threshold())

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
    0.0,
)
os.makedirs(out_dir, exist_ok=True)
t0 = pc()
result = S.run_shard(task)
wall = pc() - t0
import resource

print(
    "result",
    result[1:],
    f"wall {wall:.1f}s",
    f"maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB",
)
for gen in sorted(gc_spent):
    print(
        f"gc gen{gen}: {gc_count[gen]} collections, {gc_spent[gen]:.1f}s "
        f"({100 * gc_spent[gen] / wall:.1f}%)"
    )
for label, s in spent.most_common():
    print(
        f"{label:30s} {s:7.1f}s {100 * s / wall:5.1f}%  "
        f"{calls[label]:8d} calls {1e6 * s / calls[label]:8.1f} us"
    )
