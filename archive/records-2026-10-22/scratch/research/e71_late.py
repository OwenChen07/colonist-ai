"""E71 step 0: nets in the bot's chair from people's own late positions
(e71_starts.py's starts.jsonl), against a fixed people model or another net,
one game a start, seed SEED + start index (so arms pair by start and dice).

    .venv/bin/python data/orchestrator/scratch/research/e71_late.py NAME A B \
        [--limit N] [--local W]

A sits in the bot's chair, B in the person's. --local W plays here on W
processes instead of the fleet (at COLONIST_MATCH_NICE 19). Games go to
data/orchestrator/e71/NAME.jsonl, lines to e71/results.txt.
"""

import argparse
import dataclasses
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
OUT = f"{ROOT}/data/orchestrator/e71"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
SEED = 7_100_000_000
NETS = {
    "r73": f"Z:{ROOT}/runs/g1/nets/round_073.npz:200",
    "r152": f"Z:{ROOT}/runs/g1/nets/round_152.npz:200",
    "e54": f"Z:{ROOT}/models/e54/net.npz:200",
    "people": f"Z:{ROOT}/data/orchestrator/e68/people_net.npz:200",
}


def log(line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(f"{OUT}/results.txt", "a") as out:
        out.write(f"[{stamp}] {line}\n")


def ci(xs):
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1))
    return f"{m:.1%} ± {1.96 * sd / math.sqrt(len(xs)):.1%} (n {len(xs)})"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--local", type=int, default=None)
    p.add_argument("--starts", default="starts", help="e71/STARTS.jsonl")
    args = p.parse_args()
    a, b = NETS.get(args.a, args.a), NETS.get(args.b, args.b)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    os.environ.setdefault("COLONIST_MATCH_NICE", "19")
    os.nice(19 - os.nice(0))
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import _run

    starts = [json.loads(line) for line in open(f"{OUT}/{args.starts}.jsonl")]
    starts = starts[: args.limit]
    tasks = [
        (a, b, SEED + i, True, {}, False, {"start": s["start"], "a": s["bot"]})
        for i, s in enumerate(starts)
    ]
    hosts = None if args.local else (distributed.worker_hosts() or None)
    log(f"{args.name}: {a} (bot's chair) vs {b}, {len(tasks)} late starts")
    began = time.time()
    games = list(_run(tasks, args.local, hosts))
    log(f"{args.name}: {len(games)} games in {time.time() - began:.0f} s")
    with open(f"{OUT}/{args.name}.jsonl", "w") as out:
        for s, g in zip(starts, games):
            row = dataclasses.asdict(g)
            row.update({k: s[k] for k in ("capture", "bot_won", "opp_mmr", "cut")})
            out.write(json.dumps(row) + "\n")
    bands = {
        "all": lambda m: True,
        "2100+": lambda m: m is not None and m >= 2100,
        "<2100": lambda m: m is not None and m < 2100,
    }
    for band, keep in bands.items():
        pairs = [
            (g.score, s["bot_won"]) for s, g in zip(starts, games) if keep(s["opp_mmr"])
        ]
        if pairs:
            log(
                f"  {args.name} {band}: net {ci([x for x, _ in pairs])}; "
                f"live {ci([float(y) for _, y in pairs])}"
            )


if __name__ == "__main__":
    main()
