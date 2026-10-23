"""Robber self-block benchmark: bot robber decisions in live games where some
legal tile covers our own pips while another tile blocks the opponent at least
as much and none of ours. For each such position, the share of choices that
are such a needless self-block: live, and by NET with search at SIMS.
Usage: robber_bench.py NET SIMS [LAST] [MAX] [WORKERS]"""

import json
import os
import random
import sys
from multiprocessing import Pool

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

NET, SIMS = sys.argv[1], int(sys.argv[2])
LAST = int(sys.argv[3]) if len(sys.argv) > 3 else 300
MAX = int(sys.argv[4]) if len(sys.argv) > 4 else 200
WORKERS = int(sys.argv[5]) if len(sys.argv) > 5 else 4


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


def bad_coords(game, me, opp):
    """legal robber tiles that are needless self-blocks"""
    b = game.state.board
    coords = {
        a.value[0]
        for a in game.playable_actions
        if a.action_type == ActionType.MOVE_ROBBER
    }
    cost = {c: (blocked(b, c, opp), blocked(b, c, me)) for c in coords}
    return {
        c
        for c, (o, w) in cost.items()
        if w > 0 and any(w2 == 0 and o2 >= max(o, 1) for o2, w2 in cost.values())
    }


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.found = []

    def before(self, game):
        pass

    def step(self, game, decision):
        if (
            decision.action_type != ActionType.MOVE_ROBBER
            or col(decision.color) != self.you
        ):
            return
        bad = bad_coords(game, self.you, self.them)
        if bad:
            self.found.append((game.copy(), decision.value[0] in bad, bad))

    def after(self, game):
        pass


_net = None


def run(args):
    global _net
    path, seed = args
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return []
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        if replay(record, observer=obs).errors:
            return []
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return []
    if _net is None:
        _net = Net.load(NET)
    out = []
    for i, (game, live_bad, bad) in enumerate(obs.found):
        search = Search(
            _net, simulations=SIMS, rng=random.Random(seed * 31 + i), noise=False
        )
        action, _ = search.run(game)
        out.append((path, live_bad, action.value[0] in bad, len(bad)))
    return out


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    caps = sorted(rows)[-LAST:]
    jobs = [
        (os.path.join("captures", rows[c]["account"], c), i) for i, c in enumerate(caps)
    ]
    with Pool(WORKERS) as pool:
        res = [r for part in pool.map(run, jobs, chunksize=4) for r in part][:MAX]
    games = {}
    for path, lb, nb, _ in res:
        games.setdefault(path, []).append((lb, nb))
    groups = list(games.values())
    rng = random.Random(1)

    def mean(gs, k):
        n = sum(len(g) for g in gs)
        return sum(x[k] for g in gs for x in g) / max(1, n)

    print(f"net {NET} sims {SIMS}: positions {len(res)} in {len(groups)} games")
    for k, label in ((0, "live bot"), (1, "offline search")):
        bs = sorted(mean(rng.choices(groups, k=len(groups)), k) for _ in range(2000))
        print(
            f"  {label:15} needless self-block {mean(groups, k):.3f} [{bs[50]:.3f}, {bs[1949]:.3f}]"
        )
    both = sum(lb and nb for path, lb, nb, _ in res)
    print(
        f"  both bad {both}, live only {sum(lb and not nb for _, lb, nb, _ in res)},"
        f" offline only {sum(nb and not lb for _, lb, nb, _ in res)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
