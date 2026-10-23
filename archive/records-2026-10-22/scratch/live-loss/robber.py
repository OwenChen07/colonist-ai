"""Robber play in live games: for each robber move by the bot and by the person,
the opponent pips it blocks (city counts double) as a share of the best legal
choice, own pips it blocks, and the average opponent pips under the robber per
turn. Aggregates only, no names. Usage: robber.py [LAST] [WORKERS]"""

import json
import os
import random
import sys
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 4


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def col(c):
    return getattr(c, "value", c)


def blocked(board, coord, who):
    tile = board.map.land_tiles[coord]
    total = 0
    for node in tile.nodes.values():
        b = board.buildings.get(node)
        if b and col(b[0]) == who:
            total += pips(tile.number) * (2 if "CITY" in str(b[1]) else 1)
    return total


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.moves = []  # (side, opp_blocked, best_opp, own_blocked, turn)
        self.under = {
            "bot": [],
            "person": [],
        }  # opponent pips under robber at turn start
        self.last_turn = None
        self.step_i = 0  # index of the decision in the replayed log

    def before(self, game):
        pass

    def step(self, game, decision):
        try:
            self._step(game, decision)
        finally:
            self.step_i += 1

    def _step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        b = s.board
        if s.num_turns != self.last_turn:
            self.last_turn = s.num_turns
            for c, side in self.side.items():
                # pips of this side's buildings the robber is sitting on
                self.under[side].append(blocked(b, b.robber_coordinate, c))
        if decision.action_type != ActionType.MOVE_ROBBER:
            return
        me = col(decision.color)
        if me not in self.side:
            return
        opp = self.other[me]
        coords = {
            a.value[0]
            for a in game.playable_actions
            if a.action_type == ActionType.MOVE_ROBBER
        }
        best = max(blocked(b, c, opp) - blocked(b, c, me) for c in coords)
        coord = decision.value[0]
        o, w = blocked(b, coord, opp), blocked(b, coord, me)
        # dominated: another tile blocks the opponent at least as much (and some),
        # blocks us no more, and is strictly better on one of the two
        dom = any(
            blocked(b, c, opp) >= max(o, 1)
            and blocked(b, c, me) <= w
            and (blocked(b, c, opp) > o or blocked(b, c, me) < w)
            for c in coords
        )
        # self-block: we cover our own pips though a tile exists that blocks the
        # opponent at least as much and none of ours
        selfb = w > 0 and any(
            blocked(b, c, me) == 0 and blocked(b, c, opp) >= max(o, 1) for c in coords
        )
        self.moves.append(
            (self.side[me], o, best, w, s.num_turns, dom, self.step_i, selfb)
        )

    def after(self, game):
        pass


def facts(args):
    path, bot_won = args
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors or not obs.moves:
            return None
        return {"path": path, "won": bot_won, "moves": obs.moves, "under": obs.under}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(groups, f, n=2000):
    """mean of f over all items, game-clustered bootstrap interval"""
    rng = random.Random(1)
    groups = [g for g in groups if g]

    def mean(gs):
        num = sum(f(x) for g in gs for x in g)
        den = sum(len(g) for g in gs)
        return num / max(1, den)

    m = mean(groups)
    bs = sorted(mean(rng.choices(groups, k=len(groups))) for _ in range(n))
    return f"{m:.3f} [{bs[int(0.025 * n)]:.3f}, {bs[int(0.975 * n)]:.3f}]"


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    caps = sorted(rows)[-LAST:]
    jobs = [
        (os.path.join("captures", rows[c]["account"], c), rows[c]["bot_won"])
        for c in caps
    ]
    with Pool(WORKERS) as pool:
        res = [r for r in pool.map(facts, jobs, chunksize=8) if r]
    print(f"games {len(res)}, skipped {len(jobs) - len(res)}")
    for subset, sel in (
        ("all", res),
        ("bot won", [r for r in res if r["won"]]),
        ("bot lost", [r for r in res if not r["won"]]),
    ):
        print(f"== {subset}: {len(sel)} games")
        for side in ("bot", "person"):
            g = [[m for m in r["moves"] if m[0] == side] for r in sel]
            nm = sum(len(x) for x in g)
            print(
                f"  {side:6} robber moves {nm:5}"
                f"  opp pips blocked {boot(g, lambda m: m[1])}"
                f"  best legal (opp-own) {boot(g, lambda m: m[2])}"
                f"  took best {boot(g, lambda m: float(m[1] - m[3] >= m[2]))}"
                f"  own pips blocked {boot(g, lambda m: m[3])}"
                f"  on a 0-pip tile {boot(g, lambda m: float(m[1] == 0))}"
                f"  dominated {boot(g, lambda m: float(m[5]))}"
            )
            # by turn bucket
            for lo, hi in ((0, 30), (30, 60), (60, 999)):
                gg = [[m for m in x if lo <= m[4] < hi] for x in g]
                print(
                    f"    turns {lo}-{hi}: moves {sum(len(x) for x in gg):5}"
                    f"  took best {boot(gg, lambda m: float(m[1] - m[3] >= m[2]))}"
                    f"  own blocked {boot(gg, lambda m: m[3])}"
                    f"  dominated {boot(gg, lambda m: float(m[5]))}"
                    f"  needless self-block {boot(gg, lambda m: float(m[7]))}"
                )
            print(
                f"    needless self-block, all turns {boot(g, lambda m: float(m[7]))}"
            )
        for side in ("bot", "person"):
            g = [r["under"][side] for r in sel]
            print(f"  {side:6} own pips under robber per turn {boot(g, lambda x: x)}")
    with open("data/orchestrator/live-loss/robber_rows.json", "w") as f:
        json.dump(res, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
