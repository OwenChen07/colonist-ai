"""Does the bot turn cards into buildings worse than people? Rolls 20-59 (and 1-19)
of games of 60+ rolls vs 2100+. Every change in each side's hand is put on the
action that caused it (roll, discard, robber, bank trade, builds, dev cards).
Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/conversion.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/conversion.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import player_num_resource_cards  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from long_games import boot, mean  # noqa: E402

OUT = "data/orchestrator/live-loss/conversion.json"
MIN_ROLLS = 60
WINDOWS = {"early": (1, 20), "mid": (20, 60)}
CAT = {
    ActionType.ROLL: "prod",
    ActionType.DISCARD_RESOURCE: "disc",
    ActionType.MOVE_ROBBER: "rob",
    ActionType.MARITIME_TRADE: "trade",
    ActionType.BUILD_ROAD: "road",
    ActionType.BUILD_SETTLEMENT: "sett",
    ActionType.BUILD_CITY: "city",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
    ActionType.PLAY_YEAR_OF_PLENTY: "yop",
    ActionType.PLAY_MONOPOLY: "mono",
}
KEYS = (
    "prod",
    "disc",
    "rob",
    "trade",
    "road",
    "sett",
    "city",
    "dev",
    "yop",
    "mono",
    "other",
)
COUNTS = ("n_trade", "n_road", "n_sett", "n_city", "n_dev", "n_rolls", "n_7", "n_disc")


def blank():
    return {k: 0 for k in KEYS + COUNTS + ("hand_end",)}


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.game = None
        self.rolls = 0
        self.prev = None  # (window, category, actor side, hands)
        self.t = {w: {"bot": blank(), "person": blank()} for w in WINDOWS}

    def window(self):
        for w, (lo, hi) in WINDOWS.items():
            if lo <= self.rolls < hi:
                return w
        return None

    def hands(self, s):
        return {
            self.side[col(c)]: player_num_resource_cards(s, c)
            for c in s.colors
            if col(c) in self.side
        }

    def settle(self, s):
        if self.prev is None:
            return
        w, cat, actor, before = self.prev
        self.prev = None
        if w is None:
            return
        now = self.hands(s)
        for sd in ("bot", "person"):
            self.t[w][sd][cat] += now[sd] - before[sd]
            self.t[w][sd]["hand_end"] = now[sd]
        if cat in ("trade", "road", "sett", "city", "dev") and actor:
            self.t[w][actor]["n_" + cat] += 1
        if cat == "disc" and actor:
            self.t[w][actor]["n_disc"] += 1

    def before(self, game):
        pass

    def after(self, game):
        self.game = game
        self.settle(game.state)

    def step(self, game, decision):
        self.game = game
        s = game.state
        self.settle(s)
        if s.is_initial_build_phase:
            return
        actor = self.side.get(col(decision.color))
        if decision.action_type == ActionType.ROLL:
            self.rolls += 1
            w = self.window()
            if w and actor:
                self.t[w][actor]["n_rolls"] += 1
        self.prev = (
            self.window(),
            CAT.get(decision.action_type, "other"),
            actor,
            self.hands(s),
        )


def facts(job):
    path, won, rolls = job
    if rolls < MIN_ROLLS:
        return None
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors or obs.game is None:
            if sys.argv[-1] == "debug":
                print("errors:", rep.errors[:2], obs.game is None, flush=True)
            return None
        return {"won": won, "rolls": rolls, "t": obs.t}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def table(res):
    for w in WINDOWS:
        lo, hi = WINDOWS[w]
        span = hi - lo
        for won in (False, True):
            g = [r for r in res if r["won"] == won]
            print(
                f"== rolls {lo}-{hi - 1}, bot {'won' if won else 'lost'} ({len(g)} games); cards per 10 rolls"
            )
            for sd in ("bot", "person"):
                line = ", ".join(
                    f"{k} {mean([r['t'][w][sd][k] for r in g]) * 10 / span:+.2f}"
                    for k in KEYS
                )
                cnt = ", ".join(
                    f"{k[2:]} {mean([r['t'][w][sd][k] for r in g]):.2f}"
                    for k in COUNTS[:5] + ("n_disc",)
                )
                print(f"  {sd:6} {line}")
                print(f"  {'':6} counts in window: {cnt}")

            def gap(k, rs):
                return (
                    mean([r["t"][w]["bot"][k] - r["t"][w]["person"][k] for r in rs])
                    * 10
                    / span
                )

            for k in ("prod", "disc", "rob", "trade", "dev", "road"):
                m, a, b = boot(g, lambda rs, k=k: gap(k, rs))
                print(
                    f"  gap {k:5} (bot - person, per 10 rolls): {m:+.2f} [{a:+.2f}, {b:+.2f}]"
                )

            def eff(sd, rs):
                # building units (settlement or city upgrade) per 10 cards produced
                u = sum(r["t"][w][sd]["n_sett"] + r["t"][w][sd]["n_city"] for r in rs)
                p = sum(r["t"][w][sd]["prod"] for r in rs)
                return 10 * u / p if p else float("nan")

            def lost(sd, rs):
                # cards lost to discards, robber and bank-trade overhead per 10 produced
                x = sum(
                    -(
                        r["t"][w][sd]["disc"]
                        + r["t"][w][sd]["rob"]
                        + r["t"][w][sd]["trade"]
                    )
                    for r in rs
                )
                p = sum(r["t"][w][sd]["prod"] for r in rs)
                return 10 * x / p if p else float("nan")

            for name, f in (
                ("units per 10 produced", eff),
                ("leak (disc+rob+trade) per 10 produced", lost),
            ):
                m, a, b = boot(g, lambda rs, f=f: f("bot", rs) - f("person", rs))
                print(
                    f"  {name}: bot {f('bot', g):.3f}, person {f('person', g):.3f}, gap {m:+.3f} [{a:+.3f}, {b:+.3f}]"
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
        print(f"replayed {len(res)}", flush=True)
        with open(OUT, "w") as f:
            json.dump(res, f)
        res = json.load(open(OUT))
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
