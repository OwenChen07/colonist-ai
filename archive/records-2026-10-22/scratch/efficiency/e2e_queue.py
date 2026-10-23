"""distributed.run's queue with the real selfplay --parts-stream worker (a
fake host runs locally) against one local selfplay.run: same arrays?"""

import os
import shutil
import sys
import time

import numpy as np

from catanatron.colonist import distributed, selfplay

NET = "/home/owen/colonist-ai/runs/g1/nets/round_073.npz"
BASE = os.path.abspath("data/orchestrator/scratch/efficiency/e2e")
HOST_DIR = os.path.join(BASE, "host")
OUT = os.path.join(BASE, "run", "round", "data")
REF = os.path.join(BASE, "ref")
GAMES, SEED = 70, 7_000_000
KW = dict(simulations=4, opponent_rate=0.0)


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    distributed._prepare = lambda *a: [
        sys.executable,
        "-m",
        "catanatron.colonist.selfplay",
        NET,
        HOST_DIR,
    ]
    distributed._remote_opponents = lambda *a: ["--opponents", ""]
    distributed._ssh = lambda host, key, argv: (
        [str(a) for a in argv] if "--parts-stream" in map(str, argv) else ["true"]
    )
    distributed._rsync = lambda key, source, dest: shutil.copy(
        os.path.join(HOST_DIR, "parts", os.path.basename(str(source))), dest
    )
    os.environ["COLONIST_WORKER_HOSTS"] = "w@a:2"
    os.environ.pop("COLONIST_WORKERS_FILE", None)

    start = time.time()
    distributed.run(NET, OUT, games=GAMES, seed=SEED, workers=2, **KW)
    print(f"queue: {time.time() - start:.0f} s", flush=True)
    start = time.time()
    selfplay.run(NET, REF, games=GAMES, seed=SEED, workers=4, log=lambda m: None, **KW)
    print(f"whole: {time.time() - start:.0f} s")
    for first in range(SEED, SEED + GAMES, 64):
        count = min(64, SEED + GAMES - first)
        with (
            np.load(distributed.shard_path(OUT, first, count)) as a,
            np.load(distributed.shard_path(REF, first, count)) as b,
        ):
            assert a.files == b.files, (a.files, b.files)
            for k in a.files:
                assert np.array_equal(a[k], b[k]), k
            print(
                f"shard {first} {count}: identical, {len(a['seed'])} games, "
                f"{len(a['game'])} rows"
            )
    print("parts left:", os.listdir(os.path.join(OUT, "parts")))


if __name__ == "__main__":
    main()
