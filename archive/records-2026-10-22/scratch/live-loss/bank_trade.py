"""Bank and port trades, bot vs person, over the 2100+ ranked games with
converted captures: trades per 10 own turns, the rate paid (4:1, 3:1, 2:1),
cards given per card bought, what is bought (ore share), ports owned, and cards
discarded on 7s. By outcome and length, game-clustered bootstrap 95% ranges,
plus the same-role comparison (the bot when it lost vs the person when the
person lost). Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/bank_trade.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/bank_trade.py table
"""

import json
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/bank_trade_games.json"
FIELDS = (
    "turns",
    "trades",
    "r4",
    "r3",
    "r2",
    "given",
    "got_ore",
    "got_wheat",
    "ports",
    "port_turns",
    "disc",
    "trades_late",
    "turns_late",
    "spots",
    "ports_start",
    "ports_end",
    "one",
)
LATE = 40


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.sums = {s: dict.fromkeys(FIELDS, 0) for s in ("bot", "person")}
        for t in self.sums.values():
            t["one"] = 1

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        me = col(decision.color)
        side = self.side.get(me)
        if side is None:
            return
        t = self.sums[side]
        a = decision.action_type
        if a == ActionType.ROLL:
            t["turns"] += 1
            if s.num_turns >= LATE:
                t["turns_late"] += 1
            np_ = len(s.board.get_player_port_resources(decision.color))
            t["ports"] += np_
            t["port_turns"] += 1
            t["spots"] += sum(1 for b in s.board.buildings.values() if col(b[0]) == me)
            if t["turns"] == 1:
                t["ports_start"] = np_
            t["ports_end"] = np_
        elif a == ActionType.MARITIME_TRADE:
            v = decision.value
            give = sum(1 for x in v[:4] if x is not None)
            t["trades"] += 1
            if s.num_turns >= LATE:
                t["trades_late"] += 1
            t[{4: "r4", 3: "r3", 2: "r2"}.get(give, "r4")] += 1
            t["given"] += give
            got = str(v[4])
            t["got_ore"] += "ORE" in got
            t["got_wheat"] += "WHEAT" in got
        elif a == ActionType.DISCARD_RESOURCE:
            t["disc"] += 1


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
        if rep.errors or not obs.sums["bot"]["turns"]:
            if sys.argv[-1] == "debug":
                print("skip:", rep.errors[:2], obs.sums["bot"], flush=True)
            return None
        return {"won": won, "rolls": rolls, **obs.sums}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if os.environ.get("BT_DEBUG") or sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def ratio(side, num, den, scale=1.0):
    def f(gs):
        d = sum(g[side][den] for g in gs)
        return scale * sum(g[side][num] for g in gs) / d if d else float("nan")

    return f


def boot(gs, f, n=1000):
    rng = random.Random(1)
    if not gs:
        return float("nan"), float("nan"), float("nan")
    bs = sorted(f(rng.choices(gs, k=len(gs))) for _ in range(n))
    return f(gs), bs[25], bs[975]


def fmt(t, d=2):
    return f"{t[0]:.{d}f} [{t[1]:.{d}f}, {t[2]:.{d}f}]"


ROWS = (
    ("trades/10 turns", "trades", "turns", 10),
    ("late trades/10 turns", "trades_late", "turns_late", 10),
    ("4:1 share", "r4", "trades", 1),
    ("2:1 share", "r2", "trades", 1),
    ("cards per card", "given", "trades", 1),
    ("bought ore share", "got_ore", "trades", 1),
    ("ports owned", "ports", "port_turns", 1),
    ("ports after setup", "ports_start", "one", 1),
    ("ports at the end", "ports_end", "one", 1),
    ("ports per building", "ports", "spots", 1),
    ("discarded/10 turns", "disc", "turns", 10),
)


def table(res):
    lengths = (
        ("under 70", 0, 70),
        ("70-90", 70, 90),
        ("90+", 90, 9999),
        ("all", 0, 9999),
    )
    for outcome, sel in (
        ("bot lost", [r for r in res if not r["won"]]),
        ("bot won", [r for r in res if r["won"]]),
    ):
        for name, lo, hi in lengths:
            gs = [r for r in sel if lo <= r["rolls"] < hi]
            print(f"== {outcome}, {name} rolls: {len(gs)} games")
            for label, num, den, sc in ROWS:
                b = boot(gs, ratio("bot", num, den, sc))
                p = boot(gs, ratio("person", num, den, sc))
                print(f"  {label:22} bot {fmt(b)}   person {fmt(p)}")
    print(
        "== same role (bot when it lost vs person when the person lost; winners likewise), all lengths"
    )
    for role, bw in (("losing side", False), ("winning side", True)):
        xs = [r for r in res if r["won"] == bw]
        ys = [r for r in res if r["won"] != bw]
        for label, num, den, sc in ROWS:
            print(
                f"  {role:12} {label:22} bot {fmt(boot(xs, ratio('bot', num, den, sc)))}"
                f"   person {fmt(boot(ys, ratio('person', num, den, sc)))}"
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
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
