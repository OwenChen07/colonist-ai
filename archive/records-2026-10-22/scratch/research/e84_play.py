"""E84 step 1: self-play from people's late positions (turns 50-90).

    .venv/bin/python data/orchestrator/scratch/research/e84_play.py OUT GAMES WORKERS [SEED]

Round 189 (the best) plays itself from data/orchestrator/e84/late_starts.jsonl.gz
with production's self-play search (400 simulations, a quarter of moves full,
24 otherwise), every game from a late start, at niceness 19.
"""

import os
import subprocess
import sys
import time

ROOT = "/home/owen/colonist-ai"
NET = "runs/g1/nets/round_189.npz"
STARTS = "data/orchestrator/e84/late_starts.jsonl.gz"


def main():
    out, games, workers = sys.argv[1], sys.argv[2], sys.argv[3]
    seed = sys.argv[4] if len(sys.argv) > 4 else "8400000000"
    os.nice(19)
    cmd = [".venv/bin/python", "-m", "catanatron.colonist.selfplay", NET, out]
    cmd += ["--games", games, "--seed", seed, "--workers", workers]
    cmd += ["--simulations", "400", "--full-rate", "0.25", "--fast-simulations", "24"]
    cmd += ["--human-starts", STARTS, "--human-start-rate", "1.0"]
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    t0 = time.time()
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)
    print(f"{games} games in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
