"""Opening production in self-play, to compare with live (opening.py). From each
game's first row after setup: both sides' pips, ore+wheat and wood+brick pips
(from the static node production), and who won. Self-play is symmetric, so the
win rates are for the side with more (of a kind) by the bucket. Also the live
rows (opening_rows.json) in the same form. Usage: sp_opening.py ROUND [ROUND..]"""

import glob
import json
import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.loss_report import wilson

ROUNDS = [int(a) for a in sys.argv[1:]] or [72]
BOARD = E.NUM_TILES + E.NUM_NODES * 4 + E.NUM_EDGES * 2
INITIAL = BOARD + E.PLAYER_SIZE * 2 + E.MY_EXTRA  # is_initial_build_phase
PROD_AT = E.NUM_TILES * 7
# RESOURCES order in the encoding
R = [getattr(r, "value", r) for r in E.RESOURCES]
OW = [R.index("ORE"), R.index("WHEAT")]
WB = [R.index("WOOD"), R.index("BRICK")]


def side_facts(static, dyn, side):
    prod = static[PROD_AT : PROD_AT + E.NUM_NODES * 5].reshape(E.NUM_NODES, 5) * 5
    tot = np.zeros(5)
    for n in range(E.NUM_NODES):
        base = E.NUM_TILES + 4 * n + 2 * side
        if dyn[base] > 0.5 or dyn[base + 1] > 0.5:
            tot += prod[n]
    return {"pips": tot.sum(), "ow": tot[OW].sum(), "wb": tot[WB].sum()}


def games_of(path, kind):
    z = np.load(path)
    hs, ho = z["human_start"], z["human_opponent"]
    g, dyn, zz, st = z["game"], z["dynamic"], z["z"], z["static"]
    out = []
    first = {}
    for i in range(len(g)):
        gi = int(g[i])
        if gi in first or dyn[i, INITIAL] > 0.5:
            continue
        first[gi] = i
    for gi, i in first.items():
        if ho[gi]:
            continue
        if (kind == "bot") == bool(hs[gi]):
            continue
        if zz[i] == 0:
            continue
        d = dyn[i].astype(np.float32)
        out.append(
            {
                "me": side_facts(st[gi], d, 0),
                "them": side_facts(st[gi], d, 1),
                "won": zz[i] > 0,
            }
        )
    return out


def boot(vals, n=2000):
    rng = random.Random(1)
    m = sum(vals) / len(vals)
    bs = sorted(sum(rng.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return f"{m:.2f} [{bs[int(0.025 * n)]:.2f}, {bs[int(0.975 * n)]:.2f}]"


def report(name, rows, a="me", b="them", symmetric=True):
    print(f"== {name}: {len(rows)} games")
    for k in ("pips", "ow", "wb"):
        print(
            f"  {k}: {a} mean {boot([r[a][k] for r in rows])}  {b} mean {boot([r[b][k] for r in rows])}"
        )
    for k in ("pips", "ow", "wb"):
        for lo in (3, 5):
            ahead = behind = 0
            n = 0
            for r in rows:
                d = r[a][k] - r[b][k]
                if d >= lo:
                    n += 1
                    ahead += r["won"]
                elif d <= -lo and symmetric:
                    n += 1
                    ahead += not r["won"]
            print(f"  side ahead by >={lo} {k}: n {n} wins {wilson(ahead, n)}")
        if not symmetric:
            for lo in (3,):
                sel = [r for r in rows if r[a][k] - r[b][k] >= lo]
                sel2 = [r for r in rows if r[a][k] - r[b][k] <= -lo]
                sel3 = [r for r in rows if abs(r[a][k] - r[b][k]) < lo]
                print(
                    f"  {k} bot ahead>={lo}: {wilson(sum(r['won'] for r in sel), len(sel))}"
                    f"  even: {wilson(sum(r['won'] for r in sel3), len(sel3))}"
                    f"  behind: {wilson(sum(r['won'] for r in sel2), len(sel2))}"
                )


def main():
    for kind in ("bot", "human_start"):
        rows = []
        for rd in ROUNDS:
            for p in sorted(glob.glob(f"runs/g1/round_{rd:03d}/data/shard_*.npz")):
                try:
                    rows += games_of(p, kind)
                except (OSError, ValueError, KeyError):
                    pass
        report(f"self-play rounds {ROUNDS}, {kind} openings (first placer = me)", rows)
    with open("data/orchestrator/live-loss/opening_rows.json") as f:
        live = json.load(f)
    report("live (bot vs person)", live, "bot", "person", symmetric=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
