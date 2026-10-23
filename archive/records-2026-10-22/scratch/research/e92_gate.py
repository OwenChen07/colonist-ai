"""E92: a net from outside the loop through the production gate's checks
against the best (round 208), with train.sh's flags and loop.py's rules:

1. match: confidence gate, 200 sims, 4000 games (futility stop after 1000 if
   the top of the 95% interval is under 50.5%), a near miss (lower bound under
   50%, score >= 50.4%) plays 4000 more on other boards; pass = lower >= 50%.
2. anchor: round 13, 2000 games, loop's anchor seed; >= best's 0.668 - 0.02.
3. people model (e81/people_net.npz): 4000 games, loop's people seed;
   >= best's 0.5743 - 0.02.
4. live value: MSE on data/human/holdout/value <= best's + 0.01 (lead's
   wording), and the confidence gate's paired game comparison (95% lower
   bound of the MSE / accuracy gain not under -0.01).
Not run: the private tactical check and the AB:2 reading (never blocks).

    .venv/bin/python data/orchestrator/scratch/research/e92_gate.py CANDIDATE
"""

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
OUT = f"{ROOT}/data/orchestrator/e92/gate.txt"
BEST = f"{ROOT}/runs/g1/nets/round_208.npz"
ANCHOR = f"{ROOT}/runs/g1/nets/round_013.npz"
PEOPLE = f"{ROOT}/data/orchestrator/e81/people_net.npz"
LIVE = f"{ROOT}/data/human/holdout/value"
SIMS = 200
SEED = 9_200_000_000  # match boards no production round has used
LOOP_SEED = 3_000_000_000  # loop.py's --seed default; train.sh doesn't set it
BEST_ANCHOR, BEST_PEOPLE = 0.668, 0.5743


def log(line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(OUT, "a") as out:
        out.write(f"[{stamp}] {line}\n")


def main():
    cand = sys.argv[1]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ["COLONIST_WORKERS_FILE"] = f"{ROOT}/scripts/vm/workers.env"
    os.environ.setdefault("COLONIST_MATCH_NICE", "19")
    os.nice(19 - os.nice(0))
    from catanatron.colonist import distributed
    from catanatron.colonist.calibrate import (
        calibrate,
        load_value_shards,
        paired_game_comparison,
    )
    from catanatron.colonist.evaluate import match
    from catanatron.colonist.net import Net

    for path in (cand, BEST, ANCHOR, PEOPLE):
        if not os.path.isfile(path):
            raise SystemExit(f"missing {path}")
    hosts = distributed.worker_hosts() or None
    c, b = f"Z:{cand}:{SIMS}", f"Z:{BEST}:{SIMS}"
    log(f"E92: {cand} vs {BEST} at {SIMS} sims, seed {SEED}")
    passes = {}

    # 4. live value first: no fleet, a minute.
    data = load_value_shards([LIVE])
    cn, bn = Net.load(cand), Net.load(BEST)
    cm, bm = calibrate(cn, data), calibrate(bn, data)
    paired = paired_game_comparison(cn, bn, data)
    ok_mse = cm["mse"] <= bm["mse"] + 0.01
    ok_pair = paired["mse"]["lower"] >= -0.01 and paired["accuracy"]["lower"] >= -0.01
    passes["live"] = ok_mse and ok_pair
    log(
        f"  live: {cm['positions']} positions, {cm['games']} shards; MSE {cm['mse']:.4f} "
        f"vs best {bm['mse']:.4f} (limit +0.01: {'ok' if ok_mse else 'FAIL'}); "
        f"accuracy {cm['accuracy']:.4f} vs {bm['accuracy']:.4f}; paired {paired} "
        f"({'ok' if ok_pair else 'FAIL'})"
    )

    # 1. the match.
    show = lambda r: log(f"  match {r.n} games: {r.score:.2%} ± {r.margin:.2%}")
    opts = dict(progress=show, hosts=hosts)
    result = match(c, b, games=1000, seed=SEED + 500_000, **opts)
    if result.score + result.margin >= 0.505:
        rest = match(c, b, games=3000, seed=SEED + 500_000 + 500, **opts)
        result.games.extend(rest.games)
        if result.score - result.margin < 0.50 and result.score >= 0.504:
            log(f"  near miss at {result.score:.2%}; 4000 more games")
            more = match(c, b, games=4000, seed=SEED + 750_000, **opts)
            result.games.extend(more.games)
    else:
        log("  futility stop after 1000 games")
    lower = result.score - result.margin
    passes["match"] = lower >= 0.50
    log(
        f"  match: {result.summary()}; lower bound {lower:.3%} "
        f"({'ok' if passes['match'] else 'FAIL'}, needs 50%)"
    )

    # 2-3. anchor and people model.
    for key, opp, games, ref, fixed in (
        ("anchor", ANCHOR, 2000, BEST_ANCHOR, 700_000_000),
        ("people", PEOPLE, 4000, BEST_PEOPLE, 600_000_000),
    ):
        # the loop's own boards (seed 3e9 + fixed), as round 208 played them
        r = match(
            c, f"Z:{opp}:{SIMS}", games=games, seed=LOOP_SEED + fixed, hosts=hosts
        )
        passes[key] = r.score >= ref - 0.02
        log(
            f"  {key}: {r.summary()}; {r.score:.4f} vs best's {ref} "
            f"({'ok' if passes[key] else 'FAIL'}, floor {ref - 0.02:.4f})"
        )

    verdict = "PASSES" if all(passes.values()) else "FAILS"
    log(f"E92: {verdict} ({', '.join(f'{k} {v}' for k, v in passes.items())})")


if __name__ == "__main__":
    main()
