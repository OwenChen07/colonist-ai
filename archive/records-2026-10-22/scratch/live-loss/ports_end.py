"""Ports held at game end vs win, at equal building count, in the 2100+ ranked
games with converted captures (research's question, Oct 21, 2:00 PM). Each side's
final settlements, cities, port kinds (2:1 count) and outcome. Win rate by
port/no port within each building-spot stratum, plus a stratum-weighted
difference with a bootstrap 95% range. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/ports_end.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/ports_end.py table
"""

import json
import os
import random
import sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import CITY, SETTLEMENT  # noqa: E402
from catanatron.state_functions import get_actual_victory_points  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/ports_end_games.json"


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.cc = {}
        self.game = None

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        side = self.side.get(col(decision.color))
        if side:
            self.cc[side] = decision.color


def side_facts(game, color):
    b = game.state.board
    s = c = 0
    for _, (bc, kind) in b.buildings.items():
        if bc == color:
            s += kind == SETTLEMENT
            c += kind == CITY
    ports = b.get_player_port_resources(color)
    return {
        "s": s,
        "c": c,
        "ports": len(ports),
        "vp": get_actual_victory_points(game.state, color),
        "two": sum(1 for p in ports if p is not None),
    }


def facts(job):
    path, won, rolls = job
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors or len(obs.cc) != 2 or obs.game is None:
            return None
        return {
            "won": won,
            "rolls": rolls,
            "bot": side_facts(obs.game, obs.cc["bot"]),
            "person": side_facts(obs.game, obs.cc["person"]),
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def sides(res, who):
    """(won, spots, cities, has_port, two) per side"""
    out = []
    for r in res:
        f = r[who]
        w = r["won"] if who == "bot" else not r["won"]
        out.append((int(w), f["s"] + f["c"], f["c"], int(f["ports"] > 0), f["two"]))
    return out


def weighted_diff(rows, key):
    """port minus no-port win rate, weighted by the smaller arm in each stratum"""
    st = defaultdict(lambda: [[0, 0], [0, 0]])
    for r in rows:
        a = st[key(r)][r[3]]
        a[0] += r[0]
        a[1] += 1
    num = den = 0.0
    for no, yes in st.values():
        if no[1] and yes[1]:
            w = min(no[1], yes[1])
            num += w * (yes[0] / yes[1] - no[0] / no[1])
            den += w
    return 100 * num / den if den else float("nan")


def boot(rows, f, n=1000):
    rng = random.Random(1)
    bs = sorted(f(rng.choices(rows, k=len(rows))) for _ in range(n))
    return f(rows), bs[25], bs[975]


def table(res):
    print(f"{len(res)} games")
    if len(sys.argv) > 2 and sys.argv[2] == "full":
        res = [r for r in res if max(r["bot"]["vp"], r["person"]["vp"]) >= 10]
        print(f"played to 10 points: {len(res)}")
    for who in ("person", "bot"):
        rows = sides(res, who)
        n = len(rows)
        print(f"== {who}: {n} sides, win {100 * sum(r[0] for r in rows) / n:.1f}%")
        for label, sel in (("winners", 1), ("losers", 0)):
            g = [r for r in rows if r[0] == sel]
            print(
                f"  {label}: {len(g)}, no port {100 * sum(1 for r in g if not r[3]) / len(g):.1f}%,"
                f" 2:1 kinds {sum(r[4] for r in g) / len(g):.2f}"
            )
        by = defaultdict(lambda: [[0, 0], [0, 0]])
        for r in rows:
            a = by[r[1]][r[3]]
            a[0] += r[0]
            a[1] += 1
        for spots in sorted(by):
            no, yes = by[spots]
            if no[1] + yes[1] < 20:
                continue
            fy = f"{100 * yes[0] / yes[1]:.1f}% ({yes[1]})" if yes[1] else "-"
            fn = f"{100 * no[0] / no[1]:.1f}% ({no[1]})" if no[1] else "-"
            print(f"  {spots} spots: port {fy}  no port {fn}")
        d1 = boot(rows, lambda rs: weighted_diff(rs, lambda r: r[1]))
        d2 = boot(rows, lambda rs: weighted_diff(rs, lambda r: (r[1], r[2])))
        print(
            f"  port - no port, same spots: {d1[0]:+.1f} [{d1[1]:+.1f}, {d1[2]:+.1f}] points"
        )
        print(
            f"  same settlements+cities:    {d2[0]:+.1f} [{d2[1]:+.1f}, {d2[2]:+.1f}] points"
        )
        for lo, hi in ((0, 90), (90, 999)):
            sub = [sr for sr, r in zip(rows, res) if lo <= r["rolls"] < hi]
            if len(sub) > 50:
                d = boot(sub, lambda rs: weighted_diff(rs, lambda r: (r[1], r[2])))
                print(
                    f"  rolls {lo}-{hi} ({len(sub)}): {d[0]:+.1f} [{d[1]:+.1f}, {d[2]:+.1f}]"
                )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2 and sys.argv[2] != "debug":
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
