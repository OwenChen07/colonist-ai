"""Where does the bot's production gap in lost games come from? Games of 60+ rolls
vs 2100+. Over rolls 20-59 (and 1-19), for each side: the board's expected cards
a roll (pips of its buildings, cities doubled, /36), the part the robber blocks,
and the cards the rolls actually gave (dice luck = actual - expected unblocked).
Plus each side's pips at roll 1, 20 and 40. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/prod_split.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/prod_split.py table
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
from e75_robber import col, incomes, jobs  # noqa: E402
from long_games import boot, mean  # noqa: E402

OUT = "data/orchestrator/live-loss/prod_split.json"
MIN_ROLLS = 60
WINDOWS = {"early": (1, 20), "mid": (20, 60)}
SNAP = (1, 20, 40)
KEYS = ("board", "blk", "act", "n")


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.rolls = 0
        self.prev = None  # (window, hands before the roll)
        self.t = {
            w: {sd: dict.fromkeys(KEYS, 0.0) for sd in ("bot", "person")}
            for w in WINDOWS
        }
        self.snap = {}

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
        w, before = self.prev
        self.prev = None
        now = self.hands(s)
        for sd in ("bot", "person"):
            self.t[w][sd]["act"] += now[sd] - before[sd]

    def before(self, game):
        pass

    def after(self, game):
        self.settle(game.state)

    def step(self, game, decision):
        s = game.state
        self.settle(s)
        if s.is_initial_build_phase or decision.action_type != ActionType.ROLL:
            return
        self.rolls += 1
        inc = incomes(s.board)
        rob = s.board.robber_coordinate
        if self.rolls in SNAP:
            self.snap[self.rolls] = {
                sd: sum(inc.get(c, {}).values()) for c, sd in self.side.items()
            }
        w = self.window()
        if w is None:
            return
        for c, sd in self.side.items():
            d = inc.get(c, {})
            t = self.t[w][sd]
            t["board"] += sum(d.values()) / 36
            t["blk"] += d.get(rob, 0) / 36
            t["n"] += 1
        self.prev = (w, self.hands(s))


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
        if rep.errors or len(obs.snap) < len(SNAP):
            return None
        return {
            "won": won,
            "rolls": rolls,
            "t": obs.t,
            "snap": {str(k): v for k, v in obs.snap.items()},
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def table(res):
    for w, (lo, hi) in WINDOWS.items():
        for won in (False, True):
            g = [r for r in res if r["won"] == won]
            print(
                f"== rolls {lo}-{hi - 1}, bot {'won' if won else 'lost'} ({len(g)} games); cards per 10 rolls"
            )

            def per10(r, sd, k):
                n = r["t"][w][sd]["n"]
                return 10 * r["t"][w][sd][k] / n if n else 0.0

            def parts(r, sd):
                board, blk, act = (per10(r, sd, k) for k in ("board", "blk", "act"))
                return {
                    "board": board,
                    "robber": -blk,
                    "luck": act - (board - blk),
                    "actual": act,
                }

            for sd in ("bot", "person"):
                print(
                    f"  {sd:6} "
                    + ", ".join(
                        f"{k} {mean([parts(r, sd)[k] for r in g]):+.2f}"
                        for k in ("board", "robber", "luck", "actual")
                    )
                )
            for k in ("actual", "board", "robber", "luck"):
                m, a, b = boot(
                    g,
                    lambda rs, k=k: mean(
                        [parts(r, "bot")[k] - parts(r, "person")[k] for r in rs]
                    ),
                )
                print(f"  gap {k:6} (bot - person): {m:+.2f} [{a:+.2f}, {b:+.2f}]")
    print("== pips a roll (cities doubled) at roll 1 / 20 / 40")
    for won in (False, True):
        g = [r for r in res if r["won"] == won]
        cells = []
        for k in map(str, SNAP):
            m, a, b = boot(
                g,
                lambda rs, k=k: mean(
                    [r["snap"][k]["bot"] - r["snap"][k]["person"] for r in rs]
                ),
            )
            bot = mean([r["snap"][k]["bot"] for r in g])
            per = mean([r["snap"][k]["person"] for r in g])
            cells.append(
                f"roll {k}: bot {bot:.1f} person {per:.1f} gap {m:+.2f} [{a:+.2f}, {b:+.2f}]"
            )
        print(f"  {'won ' if won else 'lost'} ({len(g)}): " + "; ".join(cells))
    print("== win rate by opening pip gap (bot - person at roll 1)")
    for name, lo, hi in (
        ("<= -3", -99, -2.5),
        ("-2..-1", -2.5, -0.5),
        ("0", -0.5, 0.5),
        ("+1..+2", 0.5, 2.5),
        (">= +3", 2.5, 99),
    ):
        g = [
            r
            for r in res
            if lo <= r["snap"]["1"]["bot"] - r["snap"]["1"]["person"] < hi
        ]
        if g:
            m, a, b = boot(g, lambda rs: mean([1.0 if r["won"] else 0.0 for r in rs]))
            print(
                f"  {name:7} n {len(g):4}  win {100 * m:.1f}% [{100 * a:.1f}, {100 * b:.1f}]"
            )
    print(
        "== win rate by mid-game dice luck gap (bot - person, rolls 20-59, per 10 rolls)"
    )

    def lk(r):
        out = []
        for sd in ("bot", "person"):
            t = r["t"]["mid"][sd]
            out.append(
                10 * (t["act"] - t["board"] + t["blk"]) / t["n"] if t["n"] else 0.0
            )
        return out[0] - out[1]

    for name, lo, hi in (
        ("< -2", -99, -2),
        ("-2..-0.5", -2, -0.5),
        ("-0.5..0.5", -0.5, 0.5),
        ("0.5..2", 0.5, 2),
        ("> 2", 2, 99),
    ):
        g = [r for r in res if lo <= lk(r) < hi]
        if g:
            m, a, b = boot(g, lambda rs: mean([1.0 if r["won"] else 0.0 for r in rs]))
            print(
                f"  {name:9} n {len(g):4}  win {100 * m:.1f}% [{100 * a:.1f}, {100 * b:.1f}]"
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
