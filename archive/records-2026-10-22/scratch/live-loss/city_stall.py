"""Why does the bot end long losses with so few cities? Over 2100+ games with
converted captures: each side's ore and wheat income a roll (robber hex left
out), the turn of its first city, and its turns where a city was legal: did it
build one that turn, and if not, what did it spend on instead.
Usage: city_stall.py [WORKERS [FIRST_N]] | city_stall.py table"""

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

OUT = "data/orchestrator/live-loss/city_stall.json"
FROM_TURN = 20
SPEND = {
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_ROAD: "road",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
    ActionType.MARITIME_TRADE: "trade",
}


def ow_income(board, c):
    rob = board.robber_coordinate
    out = {"ORE": 0, "WHEAT": 0}
    for coord, tile in board.map.land_tiles.items():
        r = getattr(tile.resource, "value", tile.resource)
        if coord == rob or r not in out:
            continue
        p = pips(tile.number)
        for node in tile.nodes.values():
            b = board.buildings.get(node)
            if b and col(b[0]) == c:
                out[r] += p * (2 if "CITY" in str(b[1]) else 1)
    return out


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        z = lambda: {
            "rolls": 0,
            "ore": 0,
            "wheat": 0,
            "first_city": None,
            "city_turns": 0,
            "city_built": 0,
            "skip": {},
            "cities_t60": None,
        }
        self.s = {"bot": z(), "person": z()}
        self.turn = None  # (side, num_turns, city legal seen, built, spends)

    def before(self, game):
        pass

    def close(self):
        if self.turn and self.turn[2]:
            t = self.s[self.turn[0]]
            t["city_turns"] += 1
            if self.turn[3]:
                t["city_built"] += 1
            else:
                key = "+".join(sorted(self.turn[4])) or "nothing"
                t["skip"][key] = t["skip"].get(key, 0) + 1
        self.turn = None

    def after(self, game):
        self.close()

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase:
            return
        c = col(d.color)
        side = self.side.get(c)
        if side is None:
            return
        n = s.num_turns
        if self.turn is None or self.turn[1] != n or self.turn[0] != side:
            self.close()
            self.turn = [side, n, False, False, set()]
        t = self.s[side]
        if d.action_type == ActionType.ROLL and n >= FROM_TURN:
            for cc, sd in self.side.items():
                inc = ow_income(s.board, cc)
                u = self.s[sd]
                u["rolls"] += 1
                u["ore"] += inc["ORE"]
                u["wheat"] += inc["WHEAT"]
        if n < FROM_TURN:
            if d.action_type == ActionType.BUILD_CITY and t["first_city"] is None:
                t["first_city"] = n
            return
        if any(a.action_type == ActionType.BUILD_CITY for a in game.playable_actions):
            self.turn[2] = True
        if d.action_type == ActionType.BUILD_CITY:
            self.turn[3] = True
            if t["first_city"] is None:
                t["first_city"] = n
        elif self.turn[2] and d.action_type in SPEND:
            self.turn[4].add(SPEND[d.action_type])


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
        if rep.errors or not obs.s["bot"]["rolls"]:
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


def ratio(side, num, den):
    return lambda gs: sum(g[side][num] for g in gs) / max(
        1, sum(g[side][den] for g in gs)
    )


def table(res):
    for outcome, w in (("bot lost", False), ("bot won", True)):
        for name, lo, hi in (("under 75", 0, 75), ("75+", 75, 9999)):
            gs = [r for r in res if r["won"] == w and lo <= r["rolls"] < hi]
            print(f"== {outcome}, {name} rolls: {len(gs)} games")
            for side in ("bot", "person"):
                fc = sorted(g[side]["first_city"] or 999 for g in gs)
                none = sum(1 for g in gs if g[side]["first_city"] is None)
                skip = {}
                for g in gs:
                    for k, v in g[side]["skip"].items():
                        skip[k] = skip.get(k, 0) + v
                tot = sum(skip.values())
                top = ", ".join(
                    f"{k} {100*v/max(1,tot):.0f}%"
                    for k, v in sorted(skip.items(), key=lambda x: -x[1])[:5]
                )
                print(
                    f"  {side:6} ore/roll {boot(gs, ratio(side, 'ore', 'rolls'))}"
                    f"  wheat/roll {boot(gs, ratio(side, 'wheat', 'rolls'))}"
                    f"  first city median turn {fc[len(fc)//2] if fc else '-'} (none {none})"
                )
                ct = sum(g[side]["city_turns"] for g in gs)
                print(
                    f"         city-legal turns {ct} ({ct/max(1,len(gs)):.1f}/game), built that turn"
                    f" {boot(gs, ratio(side, 'city_built', 'city_turns'))}; when not: {top}"
                )
                nothing = lambda xs: sum(
                    g[side]["skip"].get("nothing", 0) for g in xs
                ) / max(1, sum(g[side]["city_turns"] for g in xs))
                games_n = sum(1 for g in gs if g[side]["skip"].get("nothing"))
                print(
                    f"         ended the turn with a city legal and nothing spent: {boot(gs, nothing)}"
                    f" of city-legal turns, in {games_n} games"
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
