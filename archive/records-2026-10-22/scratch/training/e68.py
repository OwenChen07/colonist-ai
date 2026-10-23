"""E68 (lead, Oct 20, 2:50 PM Toronto): round 73 and round 152 against a fixed
copy of the people model (models/human/net.npz, the net Vasos5306 plays),
200 searches a move each side, 2000 games each, same seed, on the fleet.

    nohup .venv/bin/python data/orchestrator/scratch/training/e68.py \
        > data/orchestrator/jobs/e68.log 2>&1 &

Snapshots the people model to data/orchestrator/e68/people_net.npz first (the
hourly refresh must not move under the match). Lines append to
data/orchestrator/e68/results.txt.
"""

import hashlib
import os
import shutil
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e68"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
SEED = 6_800_000_000
GAMES = 2000


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
    people = f"{OUT}/people_net.npz"
    if not os.path.exists(people):
        shutil.copy2(f"{ROOT}/models/human/net.npz", people)
        log(f"snapshot of models/human/net.npz (sha {sha(people)})")
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import match

    hosts = distributed.worker_hosts() or None
    scores = {}
    for rnd in ("073", "152"):
        net = f"{ROOT}/runs/g1/nets/round_{rnd}.npz"
        a, b = f"Z:{net}:200", f"Z:{people}:200"
        name = f"r{rnd}-vs-people"
        log(f"{name}: {a} vs {b}, {GAMES} games, seed {SEED}")
        show = lambda r, name=name: log(
            f"  {name} {r.n} games: {r.score:.2%} ± {r.margin:.2%}"
        )
        result = match(a, b, games=GAMES, seed=SEED, progress=show, hosts=hosts)
        log(f"{name}: {result.summary()}")
        scores[rnd] = result.score
    diff = scores["152"] - scores["073"]
    log(
        f"E68: round 152 {scores['152']:.2%}, round 73 {scores['073']:.2%}, "
        f"difference {diff:+.2%} (trails by 3 points or more: design a gate arm)"
    )


if __name__ == "__main__":
    main()
