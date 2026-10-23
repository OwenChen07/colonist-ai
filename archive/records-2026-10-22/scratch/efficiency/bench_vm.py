"""Time the loop's self-play command (round 68/69 flags) on a worker over ssh.

.venv/bin/python data/orchestrator/scratch/efficiency/bench_vm.py owen@10.128.0.5 256 4
"""

import subprocess
import sys
import time

host, games, workers = sys.argv[1], sys.argv[2], sys.argv[3]
key = "/home/owen/.ssh/colonist_worker_ed25519"
remote = (
    "rm -rf colonist-ai/data/bench-e19 && "
    "colonist-ai/.venv/bin/python -m catanatron.colonist.selfplay "
    "colonist-ai/models/graph1/net.npz colonist-ai/data/bench-e19 "
    f"--games {games} --seed 9190000000 --workers {workers} "
    "--simulations 400 --fast-simulations 24 --full-rate 0.25; nproc; uptime"
)
start = time.time()
r = subprocess.run(["ssh", "-i", key, "-o", "BatchMode=yes", host, remote])
wall = time.time() - start
print(
    f"exit {r.returncode}; {games} games in {wall:.0f} s = {int(games) / wall * 3600:.0f} games/h"
)
