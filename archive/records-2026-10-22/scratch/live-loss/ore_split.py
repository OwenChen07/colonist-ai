"""Where does the bot's ore gap in long 2100+ losses open: at setup or later?
Each side's ore and wheat pips (cities count twice, robber ignored) at the
first roll and at turns 20/40/60; how many buildings touch ore then; and the
share of its ore pips the robber sat on, summed over every roll.
Usage: ore_split.py [WORKERS [FIRST_N]] | ore_split.py table"""

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
from e75_robber import col, jobs, pips  # noqa: E402

OUT = "data/orchestrator/live-loss/ore_split.json"
MARKS = (0, 20, 40, 60)


def snap(board, c):
    rob = board.robber_coordinate
    out = {"ore": 0, "wheat": 0, "ore_bld": 0, "ore_rob": 0}
    seen = set()
    for coord, tile in board.map.land_tiles.items():
        r = getattr(tile.resource, "value", tile.resource)
        if r not in ("ORE", "WHEAT"):
            continue
        p = pips(tile.number)
        for node in tile.nodes.values():
            b = board.buildings.get(node)
            if b and col(b[0]) == c:
                k = p * (2 if "CITY" in str(b[1]) else 1)
                out[r.lower()] += k
                if r == "ORE":
                    if coord == rob:
                        out["ore_rob"] += k
                    if p and node not in seen:
                        seen.add(node)
                        out["ore_bld"] += 1
    return out


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.s = {
            sd: {"at": {}, "ore_sum": 0, "ore_rob_sum": 0} for sd in ("bot", "person")
        }

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase or d.action_type != ActionType.ROLL:
            return
        n = s.num_turns
        for cc, sd in self.side.items():
            x = snap(s.board, cc)
            t = self.s[sd]
            t["ore_sum"] += x["ore"]
            t["ore_rob_sum"] += x["ore_rob"]
            for m in MARKS:
                if n >= m and str(m) not in t["at"]:
                    t["at"][str(m)] = x


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
        if rep.errors or "0" not in obs.s["bot"]["at"]:
            return None
        return {"won": won, "rolls": rolls, **obs.s}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(gs, f, n=1000):
    rng = random.Random(1)
    if not gs:
        return "n=0"
    m = f(gs)
    bs = sorted(f(rng.choices(gs, k=len(gs))) for _ in range(n))
    return f"{m:.2f} [{bs[25]:.2f}, {bs[975]:.2f}]"


def mean_at(side, m, key):
    def f(gs):
        xs = [g[side]["at"][m][key] for g in gs if m in g[side]["at"]]
        return sum(xs) / max(1, len(xs))

    return f


def table(res):
    for outcome, w in (("bot lost", False), ("bot won", True)):
        for name, lo, hi in (("under 75", 0, 75), ("75+", 75, 9999)):
            gs = [r for r in res if r["won"] == w and lo <= r["rolls"] < hi]
            print(f"== {outcome}, {name} rolls: {len(gs)} games")
            for side in ("bot", "person"):
                for m in map(str, MARKS):
                    k = sum(1 for g in gs if m in g[side]["at"])
                    print(
                        f"  {side:6} turn {m:>2} (n={k}): ore pips {boot(gs, mean_at(side, m, 'ore'))}"
                        f"  ore buildings {boot(gs, mean_at(side, m, 'ore_bld'))}"
                        f"  wheat pips {boot(gs, mean_at(side, m, 'wheat'))}"
                    )
                rob = lambda xs: sum(g[side]["ore_rob_sum"] for g in xs) / max(
                    1, sum(g[side]["ore_sum"] for g in xs)
                )
                print(
                    f"  {side:6} share of ore pips under the robber, all rolls: {boot(gs, rob)}"
                )


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
