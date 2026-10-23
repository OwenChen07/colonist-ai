"""E80's self-play arm: the opening clock. Round 182 against itself, A with
20,000 simulations on the two setup settlements, B with 11,000, both 2,400
for the rest, seats alternated, on the fleet at niceness 19.

    .venv/bin/python data/orchestrator/scratch/research/e80_open.py run [--games 1000]
    .venv/bin/python data/orchestrator/scratch/research/e80_open.py report

Each game goes to data/orchestrator/e80/games.jsonl as it ends, so `report`
reads a partial match too: A's score with the 95% range, and A's score in
the first seat and in the second.
"""

import argparse
import dataclasses
import json
import math
import os
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e80"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
NET = f"{ROOT}/runs/g1/nets/round_182.npz"
A = f"Z:{NET}:2400:0:20000"
B = f"Z:{NET}:2400:0:11000"
SEED = 8_000_000_000


def log(line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(f"{OUT}/results.txt", "a") as out:
        out.write(f"[{stamp}] {line}\n")


def ci(scores):
    n = len(scores)
    if n < 2:
        return float("nan"), float("nan")
    m = sum(scores) / n
    var = sum((s - m) ** 2 for s in scores) / (n - 1)
    return m, 1.96 * math.sqrt(var / n)


def report():
    rows = [json.loads(l) for l in open(f"{OUT}/games.jsonl") if l.strip()]
    by_seed = {}
    for r in rows:
        by_seed.setdefault(r["seed"], []).append(r["score"])
    pairs = [sum(s) / 2 for s in by_seed.values() if len(s) == 2]
    m, pm = ci(pairs)
    first = [r["score"] for r in rows if r["a_first"]]
    second = [r["score"] for r in rows if not r["a_first"]]
    f, fm = ci(first)
    s, sm = ci(second)
    seat1 = (sum(first) + sum(1 - x for x in second)) / len(rows)
    print(
        f"{len(rows)} games ({len(pairs)} full pairs): 20k side {m:.1%} ± {pm:.1%} "
        f"(paired); in the first seat {f:.1%} ± {fm:.1%} ({len(first)}), "
        f"second seat {s:.1%} ± {sm:.1%} ({len(second)}); first seat overall {seat1:.1%}"
    )


def run(games):
    os.makedirs(OUT, exist_ok=True)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    os.environ.setdefault("COLONIST_MATCH_NICE", "19")
    os.nice(19 - os.nice(0))
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import match

    hosts = distributed.worker_hosts() or None
    # Resume after the boards already played (the runs are foreground chunks).
    seed = SEED
    if os.path.exists(f"{OUT}/games.jsonl"):
        done = [json.loads(l)["seed"] for l in open(f"{OUT}/games.jsonl") if l.strip()]
        seed = max(done, default=SEED - 1) + 1
    log(f"e80-open: {A} vs {B}, {games} games, seed {seed}")
    seen = [0]

    def show(r):
        with open(f"{OUT}/games.jsonl", "a") as out:
            for g in r.games[seen[0] :]:
                out.write(json.dumps(dataclasses.asdict(g)) + "\n")
        seen[0] = len(r.games)
        log(f"  e80-open {r.n} games: {r.score:.2%} ± {r.margin:.2%}")

    result = match(A, B, games=games, seed=seed, progress=show, hosts=hosts)
    show(result)
    log(f"e80-open: {result.summary()}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["run", "report"])
    p.add_argument("--games", type=int, default=1000)
    args = p.parse_args()
    run(args.games) if args.what == "run" else report()


if __name__ == "__main__":
    main()
