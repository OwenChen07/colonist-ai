"""After roll 60: what each side spends on (dev cards, cities, settlements, roads, bank
trades) and what it holds when it ends a turn, bot vs person, by outcome. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/late_buy.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/late_buy.py table
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
from catanatron.state_functions import player_key  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from long_games import boot, mean, wil  # noqa: E402

OUT = "data/orchestrator/live-loss/late_buy.json"
SNAP = 60
RES = ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
KINDS = {
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
    ActionType.BUILD_CITY: "city",
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_ROAD: "road",
    ActionType.MARITIME_TRADE: "bank",
    ActionType.PLAY_KNIGHT_CARD: "knight",
    ActionType.PLAY_MONOPOLY: "mono",
    ActionType.PLAY_YEAR_OF_PLENTY: "yop",
    ActionType.PLAY_ROAD_BUILDING: "rb",
}


def hand(s, color):
    k = player_key(s, color)
    return {r: s.player_state.get(f"{k}_{r}_IN_HAND", 0) for r in RES}


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.rolls = 0
        self.t = {
            sd: {
                "turns": 0,
                "end_hand": 0,
                "end_dev_ok": 0,
                "end_city_ok": 0,
                "end_big": 0,
                **{v: 0 for v in KINDS.values()},
            }
            for sd in ("bot", "person")
        }

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        at = decision.action_type
        if at == ActionType.ROLL:
            self.rolls += 1
        if self.rolls < SNAP:
            return
        sd = self.side.get(col(decision.color))
        if not sd:
            return
        t = self.t[sd]
        if at in KINDS:
            t[KINDS[at]] += 1
        elif at == ActionType.END_TURN:
            h = hand(s, decision.color)
            n = sum(h.values())
            t["turns"] += 1
            t["end_hand"] += n
            t["end_dev_ok"] += h["ORE"] >= 1 and h["WHEAT"] >= 1 and h["SHEEP"] >= 1
            t["end_city_ok"] += h["ORE"] >= 3 and h["WHEAT"] >= 2
            t["end_big"] += n >= 8


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
        if rep.errors or obs.rolls < SNAP + 10:
            return None
        return {"won": won, "rolls": rolls, "t": obs.t}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def table(res):
    print(f"{len(res)} games of 70+ rolls")
    fields = ("dev", "city", "settle", "road", "bank", "knight", "mono", "yop", "rb")
    for won in (None, False, True):
        sub = [r for r in res if won is None or r["won"] == won]
        tag = {None: "all", False: "bot losses", True: "bot wins"}[won]
        print(f"\n===== {tag} ({len(sub)}), per 10 own turns after roll 60")
        for who in ("bot", "person"):
            ts = [r["t"][who] for r in sub]
            turns = sum(t["turns"] for t in ts)
            line = " ".join(
                f"{f} {10 * sum(t[f] for t in ts) / turns:.2f}" for f in fields
            )
            print(f"  {who:6s} ({turns} turns): {line}")
            print(
                f"         end of turn: hand {sum(t['end_hand'] for t in ts) / turns:.2f},"
                f" could buy dev {wil(sum(t['end_dev_ok'] for t in ts), turns)},"
                f" could build city {wil(sum(t['end_city_ok'] for t in ts), turns)},"
                f" 8+ cards {wil(sum(t['end_big'] for t in ts), turns)}"
            )
        # per-game gap in dev buys per 10 turns, bot - person, bootstrapped over games
        for f in ("dev", "city", "bank"):

            def gap(rs, f=f):
                return mean(
                    [
                        10
                        * (
                            r["t"]["bot"][f] / r["t"]["bot"]["turns"]
                            - r["t"]["person"][f] / r["t"]["person"]["turns"]
                        )
                        for r in rs
                        if r["t"]["bot"]["turns"] and r["t"]["person"]["turns"]
                    ]
                )

            m, a, b = boot(sub, gap)
            print(
                f"  gap {f} per 10 turns (bot - person): {m:+.2f} [{a:+.2f}, {b:+.2f}]"
            )

        def gap_dev_ok(rs):
            return mean(
                [
                    r["t"]["bot"]["end_dev_ok"] / r["t"]["bot"]["turns"]
                    - r["t"]["person"]["end_dev_ok"] / r["t"]["person"]["turns"]
                    for r in rs
                    if r["t"]["bot"]["turns"] and r["t"]["person"]["turns"]
                ]
            )

        m, a, b = boot(sub, gap_dev_ok)
        print(
            f"  gap ends turn able to buy a dev card (bot - person): {100 * m:+.1f} pts [{100 * a:+.1f}, {100 * b:+.1f}]"
        )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = [j for j in jobs() if j[2] >= 70]
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
