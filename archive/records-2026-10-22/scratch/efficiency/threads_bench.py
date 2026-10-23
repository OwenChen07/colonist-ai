"""Two bridge-like searches on one 2-vCPU host: sims/s per process with the
default math threads and with one thread.

Run (from the repo root): .venv/bin/python threads_bench.py NET [SECONDS]
"""

import json
import os
import subprocess
import sys

THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def child(net_path, seconds, tag):
    import random
    import time

    import numpy as np

    from catanatron import Color
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.net import Net
    from catanatron.colonist.rules import new_game
    from catanatron.players import RandomPlayer

    net = Net.load(net_path)
    positions = []
    for seed in range(6):
        for ticks in (40, 120, 200):
            g = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed)
            n = 0
            while g.winning_color() is None and (
                n < ticks or len(g.playable_actions) < 2
            ):
                g.play_tick()
                n += 1
            if g.winning_color() is None:
                positions.append(g)
    print(f"{len(positions)} positions", file=sys.stderr)
    sims = 0
    spent = 0.0
    end = time.monotonic() + seconds
    i = 0
    while time.monotonic() < end:
        g = positions[i % len(positions)]
        i += 1
        search = Search(net, 400, rng=random.Random(i), noise=False)
        t = time.perf_counter()
        try:
            search.run(g)
        except ValueError:
            continue
        spent += time.perf_counter() - t
        sims += search.used
    print(json.dumps({"tag": tag, "sims": sims, "seconds": spent, "sps": sims / spent}))


def run(net_path, seconds, procs, one_thread):
    env = dict(os.environ)
    for k in THREAD_VARS:
        if one_thread:
            env[k] = "1"
        else:
            env.pop(k, None)
    children = [
        subprocess.Popen(
            [sys.executable, __file__, "--child", net_path, str(seconds), str(n)],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        for n in range(procs)
    ]
    out = [json.loads(c.communicate()[0].strip().splitlines()[-1]) for c in children]
    label = f"{procs} proc, {'1 thread' if one_thread else 'default threads'}"
    sps = [round(o["sps"]) for o in out]
    print(f"{label}: sims/s per process {sps}, total {sum(sps)}", flush=True)
    return sps


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        child(sys.argv[2], float(sys.argv[3]), sys.argv[4])
        sys.exit()
    net_path = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    print(f"cpus {os.cpu_count()}, affinity {sorted(os.sched_getaffinity(0))}")
    for rep in range(2):
        for one in (False, True):
            run(net_path, seconds, 1, one)
            run(net_path, seconds, 2, one)
