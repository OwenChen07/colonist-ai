"""Where the bot's income gap after roll 60 in long games comes from: how many
building units (settlement 1, city 2) each side has, pips per unit, and robber.
Games of 70+ rolls vs 2100+. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/income_split.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/income_split.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import CITY, SETTLEMENT, ActionType  # noqa: E402
from e75_robber import col, jobs, pips  # noqa: E402
from long_games import boot, mean  # noqa: E402

OUT = "data/orchestrator/live-loss/income_split.json"
SNAP = 60
CHECK = (1, 20, 40, 60, 80)
MIN_ROLLS = 70
# per-roll sums after SNAP: n, gross, blocked, units, settlements, cities, ore+wheat gross, kinds
F = ("n", "gross", "blk", "units", "s", "c", "ow", "kinds")


def side_income(board, color):
    gross = ow = 0
    kinds = set()
    rob = board.robber_coordinate
    blk = 0
    for coord, tile in board.map.land_tiles.items():
        p = pips(tile.number)
        if not p:
            continue
        for node in tile.nodes.values():
            b = board.buildings.get(node)
            if b and col(b[0]) == color:
                v = p * (2 if b[1] == CITY else 1)
                gross += v
                kinds.add(str(tile.resource))
                if str(tile.resource) in ("ORE", "WHEAT"):
                    ow += v
                if coord == rob:
                    blk += v
    s = c = 0
    for _, (bc, kind) in board.buildings.items():
        if col(bc) == color:
            s += kind == SETTLEMENT
            c += kind == CITY
    return {
        "gross": gross,
        "blk": blk,
        "units": s + 2 * c,
        "s": s,
        "c": c,
        "ow": ow,
        "kinds": len(kinds),
    }


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.game = None
        self.rolls = 0
        self.chk = {"bot": {}, "person": {}}
        self.late = {sd: dict.fromkeys(F, 0) for sd in ("bot", "person")}

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        s = game.state
        if s.is_initial_build_phase or decision.action_type != ActionType.ROLL:
            return
        self.rolls += 1
        if self.rolls not in CHECK and self.rolls < SNAP:
            return
        for c, sd in self.side.items():
            d = side_income(s.board, c)
            if self.rolls in CHECK:
                self.chk[sd][self.rolls] = {k: d[k] for k in ("gross", "units")}
            if self.rolls >= SNAP:
                t = self.late[sd]
                t["n"] += 1
                for k in F[1:]:
                    t[k] += d[k]


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
        if rep.errors or obs.game is None or not obs.late["bot"]["n"]:
            return None
        return {"won": won, "rolls": rolls, "chk": obs.chk, "late": obs.late}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def per(r, sd, k):
    t = r["late"][sd]
    return t[k] / t["n"]


def table(res):
    for lo, hi in ((70, 90), (90, 999), (70, 999)):
        for won in (False, True):
            g = [r for r in res if lo <= r["rolls"] < hi and r["won"] == won]
            if not g:
                continue
            tag = f"{lo}-{hi - 1 if hi < 999 else '+'} rolls, bot {'won' if won else 'lost'} ({len(g)})"
            print(tag)
            for sd in ("bot", "person"):
                line = [
                    f"{k} {mean([per(r, sd, k) for r in g]):.2f}"
                    for k in ("gross", "blk", "units", "s", "c", "ow", "kinds")
                ]
                ppu = mean(
                    [
                        per(r, sd, "gross") / per(r, sd, "units")
                        for r in g
                        if per(r, sd, "units")
                    ]
                )
                print(
                    f"  {sd:6} after 60: " + ", ".join(line) + f", pips/unit {ppu:.2f}"
                )
            for k in ("gross", "blk", "units", "ow"):
                m, a, b = boot(
                    g,
                    lambda rs, k=k: mean(
                        [per(r, "bot", k) - per(r, "person", k) for r in rs]
                    ),
                )
                print(f"  gap {k} (bot - person): {m:+.2f} [{a:+.2f}, {b:+.2f}]")

            def ppu_gap(rs):
                return mean([per(r, "bot", "gross") for r in rs]) / mean(
                    [per(r, "bot", "units") for r in rs]
                ) - mean([per(r, "person", "gross") for r in rs]) / mean(
                    [per(r, "person", "units") for r in rs]
                )

            m, a, b = boot(g, ppu_gap)
            print(f"  gap pips/unit (ratio of means): {m:+.2f} [{a:+.2f}, {b:+.2f}]")
            # split the gross gap: units part at the mean pips/unit, quality part at the mean units
            ub, up = mean([per(r, "bot", "units") for r in g]), mean(
                [per(r, "person", "units") for r in g]
            )
            gb, gp = mean([per(r, "bot", "gross") for r in g]), mean(
                [per(r, "person", "gross") for r in g]
            )
            qb, qp = gb / ub, gp / up
            print(
                f"  gross gap {gb - gp:+.2f} = units {(ub - up) * (qb + qp) / 2:+.2f} + quality {(qb - qp) * (ub + up) / 2:+.2f}"
                f"; net gap {gb - gp - mean([per(r, 'bot', 'blk') - per(r, 'person', 'blk') for r in g]):+.2f}"
            )
            cl = []
            for x in CHECK:
                h = [
                    r
                    for r in g
                    if str(x) in r["chk"]["bot"] and str(x) in r["chk"]["person"]
                ]
                gg = mean(
                    [
                        r["chk"]["bot"][str(x)]["gross"]
                        - r["chk"]["person"][str(x)]["gross"]
                        for r in h
                    ]
                )
                uu = mean(
                    [
                        r["chk"]["bot"][str(x)]["units"]
                        - r["chk"]["person"][str(x)]["units"]
                        for r in h
                    ]
                )
                cl.append(f"r{x} {gg:+.1f}/{uu:+.2f}")
            print("  gap at roll (gross pips / units): " + ", ".join(cl))


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
