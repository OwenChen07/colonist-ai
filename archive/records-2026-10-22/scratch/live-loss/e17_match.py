"""E17 controller (live-loss), scratch copy of data/orchestrator/jobs/e17-match.py.

Round 66 with the endgame root filter (ZE) against itself without it (Z),
400 sims, paired boards with seats swapped, on the self-play fleet. Chunks of
100 appended to data/orchestrator/jobs/e17-games.jsonl (resumable), 2000 games.
Writes its PID to data/orchestrator/jobs/e17.pid.
"""

import dataclasses
import json
import os
import sys
import time

ROOT = "/home/owen/colonist-ai"
os.chdir(ROOT)
sys.path.insert(0, f"{ROOT}/catanatron")
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "1")
os.environ.setdefault("COLONIST_WORKERS_FILE", f"{ROOT}/scripts/vm/workers.env")
# Not the GPU host: it trains the loop's nets between matches.
os.environ.setdefault("COLONIST_EXCLUDE_HOSTS", "owen@100.125.136.114")

from catanatron.colonist import distributed  # noqa: E402
from catanatron.colonist.evaluate import GameResult, MatchResult, match  # noqa: E402

assert distributed.__file__.startswith(f"{ROOT}/catanatron/"), distributed.__file__

NET = "runs/g1/nets/round_066.npz"
SIMS = 400
GAMES, CHUNK = 2000, 100
SEED = 6_400_000_000  # boards unused by the loop (4e9 + ...) and E18 (6.18e9 + ...)
LOCAL_WORKERS = 2  # the coordinator also runs the loop's games and E18's
OUT = "data/orchestrator/jobs/e17-games.jsonl"


def say(text):
    print(f"[{time.strftime('%FT%TZ', time.gmtime())}] {text}", flush=True)


def load():
    rows = []
    if os.path.exists(OUT):
        with open(OUT) as stream:
            for line in stream:
                row = json.loads(line)
                wall = row.pop("wall")
                rows.append((GameResult(**row), wall))
    return rows


def report(rows):
    result = MatchResult("ZE", "Z", [g for g, _ in rows])
    result.seconds = sum(w for _, w in rows)
    a_ms, b_ms = result.ms_per_move()
    say(
        f"filter vs none at {SIMS} sims: {result.score:.1%} ± {result.margin:.1%} "
        f"for the filter over {result.n} games; first seat {result.first_seat:.1%}; "
        f"{result.seconds / max(result.n, 1):.1f} s wall per game; "
        f"{a_ms:.0f} vs {b_ms:.0f} ms per move per process"
    )
    return result


def main():
    with open("data/orchestrator/jobs/e17.pid", "w") as stream:
        stream.write(f"{os.getpid()}\n")
    hosts = distributed.worker_hosts()
    say(f"hosts: {hosts}")
    rows = load()
    seed = SEED + len(rows) // 2
    while len(rows) < GAMES:
        if rows:
            report(rows)
        start = time.time()
        chunk = match(
            f"ZE:{NET}:{SIMS}",
            f"Z:{NET}:{SIMS}",
            games=CHUNK,
            seed=seed,
            workers=LOCAL_WORKERS,
            hosts=hosts,
        )
        wall = (time.time() - start) / chunk.n
        seed += CHUNK // 2
        with open(OUT, "a") as stream:
            for game in chunk.games:
                row = dataclasses.asdict(game)
                row.update(wall=wall)
                stream.write(json.dumps(row) + "\n")
                rows.append((game, wall))
    report(rows)
    say("done")


if __name__ == "__main__":
    main()
