"""Robber dwell in 2100+ games: when one side puts the robber on a hex where
the other side has a building, how many rolls it stays, who moves it off, and
whether the victim had a knight it could play and did not.
Knight facts are reliable for the bot only (the person's hand is hidden).
Usage: rob_dwell.py [WORKERS [FIRST_N]] | rob_dwell.py table"""

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

OUT = "data/orchestrator/live-loss/rob_dwell.json"


def hit(board, coord, c):
    """(victim pips on the hex, cities count twice; is it ore)"""
    tile = board.map.land_tiles.get(coord)
    if tile is None:
        return 0, False
    p = pips(tile.number)
    k = 0
    for node in tile.nodes.values():
        b = board.buildings.get(node)
        if b and col(b[0]) == c:
            k += p * (2 if "CITY" in str(b[1]) else 1)
    r = getattr(tile.resource, "value", tile.resource)
    return k, r == "ORE"


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.eps = []
        self.ep = None
        self.prev = None

    def before(self, game):
        pass

    def close(self, how):
        if self.ep:
            self.ep["end"] = how
            self.ep["knight_turns"] = len(self.ep.pop("kt"))
            self.ep.pop("last")
            self.eps.append(self.ep)
        self.ep = None

    def after(self, game):
        self.close("game end")

    def step(self, game, d):
        s = game.state
        me = col(d.color)
        t = d.action_type
        if s.is_initial_build_phase or me not in self.side:
            self.prev = (me, t)
            return
        ep = self.ep
        if t == ActionType.MOVE_ROBBER:
            if ep:
                knight = self.prev == (me, ActionType.PLAY_KNIGHT_CARD)
                who = "victim" if me == ep["victim_c"] else "placer"
                self.close(f"{who} {'knight' if knight else 'seven'}")
            coord = d.value[0]
            v = self.other[me]
            k, ore = hit(s.board, coord, v)
            if k > 0:
                self.ep = {
                    "placer": self.side[me],
                    "victim_c": v,
                    "pips": k,
                    "ore": ore,
                    "turn": s.num_turns,
                    "rolls": 0,
                    "vturns": 0,
                    "kt": set(),
                    "last": None,
                }
        elif ep:
            if t == ActionType.ROLL:
                ep["rolls"] += 1
            if me == ep["victim_c"]:
                if s.num_turns != ep["last"]:
                    ep["last"] = s.num_turns
                    ep["vturns"] += 1
                if any(
                    a.action_type == ActionType.PLAY_KNIGHT_CARD
                    for a in game.playable_actions
                ):
                    ep["kt"].add(s.num_turns)
        self.prev = (me, t)


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
        for e in obs.eps:
            e.pop("victim_c")
        return {"won": won, "rolls": rolls, "eps": obs.eps}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(gs, f, n=1000):
    rng = random.Random(1)
    if not gs:
        return "n=0"
    m = f(gs)
    bs = sorted(f(rng.choices(gs, k=len(gs))) for _ in range(n))
    return f"{m:.2f} [{bs[25]:.2f}, {bs[975]:.2f}]"


def stat(sel, num, den=lambda e: 1):
    """sum(num)/sum(den) over the selected episodes, games resampled"""

    def f(gs):
        es = [e for g in gs for e in g["eps"] if sel(e)]
        d = sum(den(e) for e in es)
        return sum(num(e) for e in es) / d if d else float("nan")

    return f


def table(res):
    groups = (
        ("all", lambda g: True),
        ("bot lost", lambda g: not g["won"]),
        ("bot won", lambda g: g["won"]),
    )
    for gname, gsel in groups:
        gs = [g for g in res if gsel(g)]
        print(f"== {gname}: {len(gs)} games")
        for placer in ("person", "bot"):
            for oname, osel in (("ore hex", True), ("other hex", False)):
                sel = lambda e, p=placer, o=osel: e["placer"] == p and e["ore"] == o
                n = sum(1 for g in gs for e in g["eps"] if sel(e))
                print(f"  {placer} robs {oname}: {n} placements")
                print(f"    rolls it stays {boot(gs, stat(sel, lambda e: e['rolls']))}")
                print(
                    f"    victim pips blocked x rolls {boot(gs, stat(sel, lambda e: e['pips'] * e['rolls']))}"
                )
                for how in (
                    "victim knight",
                    "victim seven",
                    "placer knight",
                    "placer seven",
                    "game end",
                ):
                    print(
                        f"    ended by {how:13} {boot(gs, stat(sel, lambda e, h=how: e['end'] == h))}"
                    )
                if placer == "person":
                    kn = lambda e, p=placer, o=osel: sel(e) and e["knight_turns"] > 0
                    m = sum(1 for g in gs for e in g["eps"] if kn(e))
                    print(
                        f"    bot had a playable knight on some turn: {boot(gs, stat(sel, lambda e: e['knight_turns'] > 0))}"
                    )
                    print(
                        f"    of those ({m}), bot never played it before the robber left: "
                        f"{boot(gs, stat(kn, lambda e: e['end'] != 'victim knight'))}"
                    )
                    print(
                        f"    of those, bot turns with a knight playable before it played: "
                        f"{boot(gs, stat(kn, lambda e: e['knight_turns']))}"
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
