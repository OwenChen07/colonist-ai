"""Follow-up to room.py: bot turns that could pay for a settlement and had a
spot on its roads. Per turn: best open spot's pips, whether it built, what
it did instead (city, dev, road, port/bank trade), and VP of both sides at
the turn's first decision.
Usage: room2.py [WORKERS [FIRST_N]] | room2.py table"""

import json
import os
import sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.decks import SETTLEMENT_COST_FREQDECK  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import (  # noqa: E402
    get_actual_victory_points,
    get_visible_victory_points,
    player_resource_freqdeck_contains,
)
from dev_timing import boot, mean  # noqa: E402
from e75_robber import col, jobs, pips  # noqa: E402

OUT = "data/orchestrator/live-loss/room2.json"
KINDS = {
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_CITY: "city",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
    ActionType.BUILD_ROAD: "road",
    ActionType.MARITIME_TRADE: "trade",
}


def node_pips(board, n):
    return sum(pips(t.number) for t in board.map.adjacent_tiles[n] if t.number)


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.rolls = 0
        self.turns = {}
        self.person = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        me = col(d.color)
        t = d.action_type
        if s.is_initial_build_phase or me not in self.side:
            return
        if t == ActionType.ROLL:
            self.rolls += 1
        if self.side[me] == "person":
            self.person = d.color
            return
        row = self.turns.get(s.num_turns)
        if row is None:
            row = self.turns[s.num_turns] = {
                "did": [],
                "spot": None,
                "roll": self.rolls,
            }
            row["vp"] = get_actual_victory_points(s, d.color)
            if self.person is not None:
                row["ovp"] = get_visible_victory_points(s, self.person)
        if row["spot"] is None and player_resource_freqdeck_contains(
            s, d.color, SETTLEMENT_COST_FREQDECK
        ):
            spots = s.board.buildable_node_ids(d.color)
            if spots:
                row["spot"] = max(node_pips(s.board, n) for n in spots)
        if t in KINDS:
            row["did"].append(KINDS[t])
            if t == ActionType.BUILD_SETTLEMENT:
                row["built"] = node_pips(s.board, d.value)


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
        if rep.errors:
            return None
        keys = sorted(obs.turns)
        rows = []
        for i, k in enumerate(keys):
            r = obs.turns[k]
            if r["spot"] is None:
                continue
            nxt = obs.turns[keys[i + 1]] if i + 1 < len(keys) else None
            r["next_settle"] = bool(nxt and "settle" in nxt["did"])
            rows.append(r)
        return {"won": won, "rolls": obs.rolls, "rows": rows}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def table(res):
    groups = (
        ("bot lost", lambda g: not g["won"]),
        ("bot won", lambda g: g["won"]),
    )
    for gname, gsel in groups:
        gs = [g for g in res if gsel(g)]
        rows = [r for g in gs for r in g["rows"]]
        print(
            f"== {gname}: {len(gs)} games, {len(rows)} turns able to settle with a spot"
        )

        def share(pred, sel=lambda r: True):
            def f(games):
                rs = [r for g in games for r in g["rows"] if sel(r)]
                return sum(1 for r in rs if pred(r)) / len(rs) if rs else float("nan")

            return f

        built = lambda r: "settle" in r["did"]  # noqa: E731
        print(f"  built a settlement          {boot(gs, share(built), fmt='{:.1%}')}")
        for lo, hi in ((0, 5), (5, 8), (8, 11), (11, 20)):
            sel = lambda r, lo=lo, hi=hi: lo <= r["spot"] < hi  # noqa: E731
            n = sum(1 for r in rows if sel(r))
            print(
                f"    best spot {lo:>2}-{hi - 1:<2} pips ({n:>4} turns) built {boot(gs, share(built, sel), fmt='{:.1%}')}"
            )
        for name, sel in (
            ("behind on VP", lambda r: "ovp" in r and r["vp"] < r["ovp"]),
            ("level or ahead", lambda r: "ovp" in r and r["vp"] >= r["ovp"]),
            ("before roll 40", lambda r: r["roll"] < 40),
            ("roll 40+", lambda r: r["roll"] >= 40),
        ):
            n = sum(1 for r in rows if sel(r))
            print(
                f"    {name:<26} ({n:>4} turns) built {boot(gs, share(built, sel), fmt='{:.1%}')}"
            )
        skip = [r for r in rows if not built(r)]
        c = Counter()
        for r in skip:
            ds = set(r["did"]) or {"nothing"}
            for x in ds:
                c[x] += 1
        print(f"  skipped turns: {len(skip)}; what it did (a turn can do several):")
        for k, v in c.most_common():
            print(f"    {k:<8} {v / len(skip):.1%}")
        print(
            f"    settled the next turn {sum(r['next_settle'] for r in skip) / len(skip):.1%}"
        )
        print(
            f"  best spot pips, skipped {boot(gs, mean_rows(lambda r: r['spot'], lambda r: not built(r)))}"
        )
        print(
            f"  best spot pips, built   {boot(gs, mean_rows(lambda r: r['spot'], built))}"
        )


def mean_rows(get, sel):
    def f(games):
        xs = [get(r) for g in games for r in g["rows"] if sel(r)]
        return sum(xs) / len(xs) if xs else float("nan")

    return f


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2:
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games vs 2100+", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)


if __name__ == "__main__":
    main()
