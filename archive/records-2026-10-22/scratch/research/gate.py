"""Research matches on the self-play fleet, through the loop's evaluate path
(the hosts in scripts/vm/workers.env, as the loop's promotion matches).

    .venv/bin/python data/orchestrator/scratch/research/gate.py NAME A B \
        [--games 2000] [--gate] [--seed S] [--diagnostics] [--out FILE]

A and B are evaluate specs (Z:<net>:<sims>, AB:2 ...). --gate is E38's:
2000 games, 52%+ passes, 50-52% plays 4000 more on other boards and passes
at 51% on all, under 50% stops. Lines append to --out.
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--games", type=int, default=2000)
    p.add_argument("--gate", action="store_true")
    p.add_argument("--seed", type=int, default=7_470_000_000)
    p.add_argument("--diagnostics", action="store_true")
    p.add_argument(
        "--plain-dice", action="store_true", help="two plain dice, as OpenHex"
    )
    p.add_argument("--out", default=f"{ROOT}/data/orchestrator/e47/results.txt")
    args = p.parse_args()

    def log(line):
        stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
        print(f"[{stamp}] {line}", flush=True)
        with open(args.out, "a") as out:
            out.write(f"[{stamp}] {line}\n")

    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    # Yield to the training loop's self-play and gate (efficiency, Oct 20 6:31 PM):
    # the hosts' games run at this niceness (df9c953), and so does the coordinator.
    os.environ.setdefault("COLONIST_MATCH_NICE", "19")
    os.nice(19 - os.nice(0))
    from catanatron.colonist import distributed
    from catanatron.colonist.evaluate import match

    for spec in (args.a, args.b):
        if spec.startswith("Z:") and not os.path.isfile(spec.split(":")[1]):
            raise SystemExit(f"missing {spec}")
    hosts = distributed.worker_hosts() or None
    log(f"{args.name}: {args.a} vs {args.b}, {args.games} games, seed {args.seed}")
    show = lambda r: log(f"  {args.name} {r.n} games: {r.score:.2%} ± {r.margin:.2%}")
    opts = dict(progress=show, hosts=hosts)
    if args.diagnostics:
        opts["diagnostics"] = True
    if args.plain_dice:
        opts["rules"] = {"dice": "random"}
    result = match(args.a, args.b, games=args.games, seed=args.seed, **opts)
    log(f"{args.name}: {result.summary()}")
    if not args.gate:
        return
    if result.score >= 0.52:
        log(f"{args.name}: PASSES the gate at {result.score:.2%} over {result.n}")
        return
    if result.score < 0.50:
        log(f"{args.name}: FAILS the gate at {result.score:.2%} over {result.n}")
        return
    log(f"{args.name}: near miss at {result.score:.2%}; 4000 more games")
    more = match(args.a, args.b, games=4000, seed=args.seed + 250_000, **opts)
    result.games.extend(more.games)
    verdict = "PASSES" if result.score >= 0.51 else "FAILS"
    log(
        f"{args.name}: {verdict} the gate at {result.score:.2%} ± "
        f"{result.margin:.2%} over {result.n} games (passes at 51%)"
    )


if __name__ == "__main__":
    main()
