"""E70: E54's exploiter (step 30) vs round 152, and round 152 vs itself, on the
fleet at low priority, keeping every game's record (diagnostics' "final").

    nohup .venv/bin/python data/orchestrator/scratch/research/e70.py \
        > data/orchestrator/jobs/e70.log 2>&1 &

Records go to data/orchestrator/e70/<name>.jsonl, one game a line.
"""

import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e70"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
E54 = f"Z:{ROOT}/data/orchestrator/e54/run1/iter-30/net.npz:200"
R152 = f"Z:{ROOT}/runs/g1/nets/round_152.npz:200"
MATCHES = [
    ("e54i30-vs-r152", E54, R152, 1000, 7_700_000_000),
    ("r152-vs-r152", R152, R152, 500, 7_710_000_000),
]


def main():
    os.makedirs(OUT, exist_ok=True)

    def log(line):
        stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
        print(f"[{stamp}] {line}", flush=True)
        with open(f"{OUT}/results.txt", "a") as out:
            out.write(f"[{stamp}] {line}\n")

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    os.environ.setdefault("COLONIST_MATCH_NICE", "19")
    os.nice(19 - os.nice(0))
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import match

    hosts = distributed.worker_hosts() or None
    for name, a, b, games, seed in MATCHES:
        if os.path.exists(f"{OUT}/{name}.jsonl"):
            log(f"{name}: records exist, skipped")
            continue
        log(f"{name}: {a} vs {b}, {games} games, seed {seed}")
        show = lambda r: log(f"  {name} {r.n} games: {r.score:.2%} ± {r.margin:.2%}")
        result = match(
            a, b, games=games, seed=seed, hosts=hosts, diagnostics=True, progress=show
        )
        log(f"{name}: {result.summary()}")
        with open(f"{OUT}/{name}.jsonl.part", "w") as out:
            for game in result.games:
                out.write(json.dumps(dataclasses.asdict(game)) + "\n")
        os.rename(f"{OUT}/{name}.jsonl.part", f"{OUT}/{name}.jsonl")


if __name__ == "__main__":
    main()
