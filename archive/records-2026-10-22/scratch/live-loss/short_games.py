"""Short games (<60 rolls) vs 2100+ players: how the winner scored, how fast each
side reached 5/7/9 VP, when each built its first city, dev cards bought. Compared
with 60+ roll games. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/short_games.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/short_games.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import CITY, ActionType  # noqa: E402
from catanatron.state_functions import get_actual_victory_points  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from long_games import boot, mean, side_now  # noqa: E402

OUT = "data/orchestrator/live-loss/short_games.json"
MARKS = (5, 8, 11, 14)
SNAP = (20, 30, 40)


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.cc = {}
        self.game = None
        self.rolls = 0
        self.reach = {"bot": {}, "person": {}}
        self.city1 = {}
        self.dev = {"bot": 0, "person": 0}
        self.dev30 = {}
        self.snap = {}

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        side = self.side.get(col(decision.color))
        if side:
            self.cc[side] = decision.color
        s = game.state
        if s.is_initial_build_phase:
            return
        if side and decision.action_type == ActionType.BUY_DEVELOPMENT_CARD:
            self.dev[side] += 1
        if decision.action_type != ActionType.ROLL:
            return
        self.rolls += 1
        for sd, c in self.cc.items():
            vp = get_actual_victory_points(s, c)
            for m in MARKS:
                if vp >= m and m not in self.reach[sd]:
                    self.reach[sd][m] = self.rolls
            if sd not in self.city1 and any(
                bc == c and k == CITY for bc, k in s.board.buildings.values()
            ):
                self.city1[sd] = self.rolls
        if self.rolls == 30:
            self.dev30 = dict(self.dev)
        if self.rolls in SNAP and len(self.cc) == 2:
            self.snap[self.rolls] = {
                sd: get_actual_victory_points(s, c) for sd, c in self.cc.items()
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
        end = {sd: side_now(obs.game, c) for sd, c in obs.cc.items()}
        return {
            "won": won,
            "rolls": rolls,
            "target": getattr(obs.game, "vps_to_win", None),
            "end": end,
            "reach": obs.reach,
            "city1": obs.city1,
            "dev": obs.dev,
            "dev30": obs.dev30,
            "snap": {str(k): v for k, v in obs.snap.items()},
        }
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        StopIteration,
    ):
        return None


BUCKETS = (("<45", 0, 45), ("45-59", 45, 60), ("60+", 60, 999))


def ci(g, f, pct=False):
    if not g:
        return "n/a"
    m, a, b = boot(g, lambda rs: mean([f(r) for r in rs]))
    k = 100 if pct else 1
    return (
        f"{k * m:.1f} [{k * a:.1f}, {k * b:.1f}]"
        if pct
        else f"{m:.2f} [{a:.2f}, {b:.2f}]"
    )


def finished(r):
    return (
        r["target"]
        and max(r["end"][sd]["vp"] for sd in ("bot", "person")) >= r["target"]
    )


def table(res):
    from collections import Counter

    print("targets", Counter(r["target"] for r in res))
    print(
        "== games that ended before anyone reached the target (resign/timeout/abandon)"
    )
    for name, lo, hi in BUCKETS:
        for won in (False, True):
            g = [r for r in res if lo <= r["rolls"] < hi and r["won"] == won]
            if g:
                print(
                    f"  {name:6} bot {'won ' if won else 'lost'} n {len(g):4}: unfinished "
                    f"{ci(g, lambda r: float(not finished(r)), True)}%"
                )
    print("== all games: win rate by length")
    for name, lo, hi in BUCKETS:
        g = [r for r in res if lo <= r["rolls"] < hi]
        print(
            f"  {name:6} n {len(g):4}  bot win {ci(g, lambda r: float(r['won']), True)}%"
        )
    res = [r for r in res if finished(r)]
    print(f"== FINISHED games only from here ({len(res)})")
    print(f"{len(res)} games; win rate by length")
    for name, lo, hi in BUCKETS:
        g = [r for r in res if lo <= r["rolls"] < hi]
        print(
            f"  {name:6} n {len(g):4}  bot win {ci(g, lambda r: float(r['won']), True)}%"
        )
    for name, lo, hi in BUCKETS:
        for won in (False, True):
            g = [r for r in res if lo <= r["rolls"] < hi and r["won"] == won]
            if not g:
                continue
            w, l = ("bot", "person") if won else ("person", "bot")
            print(
                f"== {name} rolls, bot {'won' if won else 'lost'} ({len(g)} games): winner = {w}"
            )
            for sd in (w, l):
                tag = "winner" if sd == w else "loser "
                e = lambda k, sd=sd: mean([r["end"][sd][k] for r in g])  # noqa: E731
                print(
                    f"  {tag} {sd:6} end vp {e('vp'):.2f}: sett {e('s'):.2f} city {e('c'):.2f} "
                    f"army {e('army'):.2f} road {e('road'):.2f} vpcards {e('vpc'):.2f} knights {e('kn'):.2f} "
                    f"dev bought {mean([r['dev'][sd] for r in g]):.2f}"
                )
            for sd in (w, l):
                cells = []
                for m in MARKS:
                    xs = [r["reach"][sd].get(str(m), r["reach"][sd].get(m)) for r in g]
                    hit = [x for x in xs if x is not None]
                    cells.append(
                        f"{m}VP roll {mean(hit):.1f} ({100 * len(hit) / len(g):.0f}%)"
                        if hit
                        else f"{m}VP none"
                    )
                c1 = [r["city1"].get(sd) for r in g if r["city1"].get(sd) is not None]
                cells.append(
                    f"1st city roll {mean(c1):.1f} ({100 * len(c1) / len(g):.0f}%)"
                    if c1
                    else "no city"
                )
                print(f"  {sd:6} " + "; ".join(cells))
            for k in map(str, SNAP):
                gg = [r for r in g if k in r["snap"]]
                if gg:
                    print(
                        f"  vp gap bot-person at roll {k} (n {len(gg)}): "
                        f"{ci(gg, lambda r, k=k: r['snap'][k]['bot'] - r['snap'][k]['person'])}"
                    )
    print("== short (<60) losses vs short wins: winner's plan shares")
    for won in (False, True):
        g = [r for r in res if r["rolls"] < 60 and r["won"] == won]
        w = "bot" if won else "person"
        print(
            f"  winner {w:6} n {len(g)}: army {ci(g, lambda r: r['end'][w]['army'], True)}%, "
            f"road {ci(g, lambda r: r['end'][w]['road'], True)}%, "
            f"vp cards>=1 {ci(g, lambda r: float(r['end'][w]['vpc'] >= 1), True)}%, "
            f"cities {ci(g, lambda r: r['end'][w]['c'])}, dev bought {ci(g, lambda r: r['dev'][w])}"
        )
    print("== dev cards bought by roll 30 (games reaching roll 30), by outcome")
    for won in (False, True):
        g = [r for r in res if r["dev30"] and r["won"] == won]
        print(
            f"  bot {'won ' if won else 'lost'} n {len(g)}: bot {ci(g, lambda r: r['dev30']['bot'])}, "
            f"person {ci(g, lambda r: r['dev30']['person'])}"
        )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2:
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}", flush=True)
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
