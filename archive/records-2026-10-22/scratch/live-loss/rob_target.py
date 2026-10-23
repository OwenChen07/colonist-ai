"""Where each side puts the robber in 2100+ games: the victim's pips on the
target, its ore+wheat+sheep pips (the dev-card resources), and the best clean
hex (none of our own buildings) for each, by outcome and game length.
Usage: rob_target.py [WORKERS [FIRST_N]] | rob_target.py table"""

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

OUT = "data/orchestrator/live-loss/rob_target.json"
DEV = ("ORE", "WHEAT", "SHEEP")


def weight(board, tile, c):
    k = 0
    for node in tile.nodes.values():
        b = board.buildings.get(node)
        if b and col(b[0]) == c:
            k += 2 if "CITY" in str(b[1]) else 1
    return k


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.rows = []
        self.prev = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        me = col(d.color)
        t = d.action_type
        if (
            t == ActionType.MOVE_ROBBER
            and not s.is_initial_build_phase
            and me in self.side
        ):
            v = self.other[me]
            board = s.board
            cands = {}
            for coord, tile in board.map.land_tiles.items():
                if coord == board.robber_coordinate:
                    continue
                p = pips(tile.number)
                r = getattr(tile.resource, "value", tile.resource)
                vp = p * weight(board, tile, v)
                cands[coord] = (
                    vp,
                    vp if r in DEV else 0,
                    p * weight(board, tile, me),
                    r,
                )
            tgt = cands.get(d.value[0], (0, 0, 0, None))
            clean = [c for c in cands.values() if c[2] == 0]
            idx = {col(c): i for i, c in enumerate(s.colors)}
            vps = {c: s.player_state[f"P{idx[c]}_VICTORY_POINTS"] for c in idx}
            self.rows.append(
                {
                    "who": self.side[me],
                    "knight": self.prev == (me, ActionType.PLAY_KNIGHT_CARD),
                    "turn": s.num_turns,
                    "vp": tgt[0],
                    "dv": tgt[1],
                    "own": tgt[2],
                    "res": tgt[3],
                    "best_v": max((c[0] for c in clean), default=0),
                    "best_dv": max((c[1] for c in clean), default=0),
                    "lead": vps[me] - vps[v],
                }
            )
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
        return {"won": won, "rolls": rolls, "rows": obs.rows}
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
    def f(gs):
        es = [e for g in gs for e in g["rows"] if sel(e)]
        d = sum(den(e) for e in es)
        return sum(num(e) for e in es) / d if d else float("nan")

    return f


def table(res):
    lengths = (("under 70", 0, 70), ("70+", 70, 9999), ("all", 0, 9999))
    for oname, won in (("bot lost", False), ("bot won", True)):
        for lname, lo, hi in lengths:
            gs = [g for g in res if g["won"] == won and lo <= g["rolls"] < hi]
            print(f"== {oname}, {lname} rolls: {len(gs)} games")
            for who in ("bot", "person"):
                # from turn 20 on; the opening robber has little to hit
                sel = lambda e, w=who: e["who"] == w and e["turn"] >= 20
                n = sum(1 for g in gs for e in g["rows"] if sel(e))
                print(f"  {who} moves the robber: {n} placements (turn 20+)")
                print(
                    f"    victim pips on target       {boot(gs, stat(sel, lambda e: e['vp']))}"
                )
                print(
                    f"    best clean hex victim pips  {boot(gs, stat(sel, lambda e: e['best_v']))}"
                )
                print(
                    f"    share of best (pips)        {boot(gs, stat(sel, lambda e: e['vp'], lambda e: e['best_v']))}"
                )
                print(
                    f"    victim ore/wheat/sheep pips {boot(gs, stat(sel, lambda e: e['dv']))}"
                )
                print(
                    f"    best clean ore/wheat/sheep  {boot(gs, stat(sel, lambda e: e['best_dv']))}"
                )
                print(
                    f"    share of best (o/w/s)       {boot(gs, stat(sel, lambda e: e['dv'], lambda e: e['best_dv']))}"
                )
                print(
                    f"    on own pips                 {boot(gs, stat(sel, lambda e: e['own'] > 0))}"
                )
                print(
                    f"    on ore hex                  {boot(gs, stat(sel, lambda e: e['res'] == 'ORE'))}"
                )
                print(
                    f"    on wheat hex                {boot(gs, stat(sel, lambda e: e['res'] == 'WHEAT'))}"
                )
                print(
                    f"    on sheep hex                {boot(gs, stat(sel, lambda e: e['res'] == 'SHEEP'))}"
                )
                print(
                    f"    mover's VP lead             {boot(gs, stat(sel, lambda e: e['lead']))}"
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
