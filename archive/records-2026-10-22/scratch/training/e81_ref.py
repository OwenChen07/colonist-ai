"""E81 (lead, Oct 21, 1:55 PM Toronto): the people-model gate check's
reference. Rounds 189 (best) and 182 (the promotion before) against a fixed
copy of the people model, 200 searches a move each side, 4000 games each, on
the fleet at niceness 19, with the seed the gate would use
(--seed 3_000_000_000 + 600_000_000).

    nohup .venv/bin/python data/orchestrator/scratch/training/e81_ref.py \
        > data/orchestrator/jobs/e81-ref.log 2>&1 &

The opponent is E68's snapshot (fit from round 152, sha 84f516847a618134),
copied to data/orchestrator/e81/people_net.npz: not the hourly-refreshed
models/human/net.npz, which is re-fit from the current best each time.
"""

import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e81"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
SEED = 3_600_000_000
GAMES = 4000


def log(line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(f"{OUT}/results.txt", "a") as out:
        out.write(f"[{stamp}] {line}\n")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def main():
    os.chdir(ROOT)
    os.makedirs(OUT, exist_ok=True)
    os.nice(19 - os.nice(0))
    people = f"{OUT}/people_net.npz"
    if not os.path.exists(people):
        shutil.copy2(f"{ROOT}/data/orchestrator/e68/people_net.npz", people)
    log(f"people model {people} (sha {sha(people)})")
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_MATCH_NICE"] = "19"
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import match

    hosts = distributed.worker_hosts() or None
    for rnd in ("189", "182"):
        net = f"{ROOT}/runs/g1/nets/round_{rnd}.npz"
        a, b = f"Z:{net}:200", f"Z:{people}:200"
        name = f"r{rnd}-vs-people"
        log(f"{name}: {GAMES} games, seed {SEED}")
        show = lambda r, name=name: (
            log(f"  {name} {r.n} games: {r.score:.2%} ± {r.margin:.2%}")
            if r.n % 500 == 0
            else None
        )
        result = match(a, b, games=GAMES, seed=SEED, progress=show, hosts=hosts)
        log(f"{name}: {result.summary()}")


if __name__ == "__main__":
    main()
