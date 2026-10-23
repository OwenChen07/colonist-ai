"""Robber self-block, stricter: does a tile exist that blocks the opponent at
least as much *of the same resource* as the chosen tile, and none of ours?
That removes the excuse "the bot was denying a resource the opponent needs".
Also splits bot self-blocks by knight vs 7, and by what the bot covers.
Usage: robber2.py [LAST] [WORKERS]"""

import json
import os
import sys
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

from robber import blocked, boot, col

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 4


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.moves = []
        self.prev = None

    def before(self, game):
        pass

    def step(self, game, decision):
        prev, self.prev = self.prev, decision
        if decision.action_type != ActionType.MOVE_ROBBER:
            return
        me = col(decision.color)
        if me not in self.side:
            return
        opp = self.other[me]
        b = game.state.board
        coords = {
            a.value[0]
            for a in game.playable_actions
            if a.action_type == ActionType.MOVE_ROBBER
        }
        res = {c: b.map.land_tiles[c].resource for c in coords}
        cost = {c: (blocked(b, c, opp), blocked(b, c, me)) for c in coords}
        coord = decision.value[0]
        o, w = cost[coord]
        loose = w > 0 and any(w2 == 0 and o2 >= max(o, 1) for o2, w2 in cost.values())
        strict = w > 0 and any(
            cost[c][1] == 0 and cost[c][0] >= max(o, 1) and res[c] == res[coord]
            for c in coords
        )
        # strictly more opponent pips and none of ours, any resource
        better = w > 0 and any(cost[c][1] == 0 and cost[c][0] > o for c in coords)
        knight = prev is not None and prev.action_type == ActionType.PLAY_KNIGHT_CARD
        self.moves.append(
            (self.side[me], loose, strict, better, knight, o, w, game.state.num_turns)
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
        if replay(record, observer=obs).errors or not obs.moves:
            return None
        return {"won": bot_won, "moves": obs.moves}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


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
    for side in ("bot", "person"):
        g = [[m for m in r["moves"] if m[0] == side] for r in res]
        print(f"== {side}: robber moves {sum(len(x) for x in g)}")
        print(f"  loose self-block        {boot(g, lambda m: float(m[1]))}")
        print(f"  same-resource self-block {boot(g, lambda m: float(m[2]))}")
        print(f"  strictly-better exists  {boot(g, lambda m: float(m[3]))}")
        for kn, label in ((True, "knight"), (False, "seven")):
            gg = [[m for m in x if m[4] == kn] for x in g]
            print(
                f"  {label:6} moves {sum(len(x) for x in gg):5}"
                f"  loose {boot(gg, lambda m: float(m[1]))}"
                f"  same-resource {boot(gg, lambda m: float(m[2]))}"
            )
    # does it go with losing? per game count of bot strict self-blocks
    for won in (True, False):
        g = [[m for m in r["moves"] if m[0] == "bot"] for r in res if r["won"] == won]
        print(
            f"bot {'won' if won else 'lost'} ({len(g)} g): same-resource self-block "
            f"{boot(g, lambda m: float(m[2]))}, own pips covered when it does "
            f"{boot([[m for m in x if m[2]] for x in g], lambda m: m[6])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
