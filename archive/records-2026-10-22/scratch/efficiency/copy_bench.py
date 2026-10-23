"""Times Game.copy and its parts on states from random-play games.

usage: copy_bench.py [games]
"""

import random
import sys
import timeit

from catanatron.colonist.selfplay import fresh_game

games = int(sys.argv[1]) if len(sys.argv) > 1 else 8
snaps = []
for s in range(games):
    g = fresh_game(1000 + s)
    rng = random.Random(s)
    step = 0
    while g.winning_color() is None and step < 1500:
        g.execute(rng.choice(g.playable_actions))
        step += 1
        if step % 25 == 0:
            snaps.append(g.copy())
print(len(snaps), "snapshots")


def per(fn):
    n = 20
    t = min(timeit.repeat(lambda: [fn(x) for x in snaps], number=n, repeat=3))
    return t / n / len(snaps) * 1e6


print(f"game.copy  {per(lambda g: g.copy()):6.2f} us")
print(f"state.copy {per(lambda g: g.state.copy()):6.2f} us")
print(f"board.copy {per(lambda g: g.state.board.copy()):6.2f} us")
print(f"dice.copy  {per(lambda g: g.state.dice.copy()):6.2f} us")
print(f"pstate     {per(lambda g: g.state.player_state.copy()):6.2f} us")
print(f"records    {per(lambda g: g.state.action_records.copy()):6.2f} us")
from collections import defaultdict

from catanatron.state import State


def bbc(g):
    return {
        color: defaultdict(list, {kind: list(ids) for kind, ids in by_kind.items()})
        for color, by_kind in g.state.buildings_by_color.items()
    }


print(f"bbc        {per(bbc):6.2f} us")
print(f"State()    {per(lambda g: State([], None, initialize=False)):6.2f} us")
print(f"deck       {per(lambda g: g.state.development_listdeck.copy()):6.2f} us")


def fast(self):
    c = State.__new__(State)
    d = c.__dict__
    d.update(self.__dict__)
    d["board"] = self.board.copy()
    d["player_state"] = self.player_state.copy()
    d["resource_freqdeck"] = self.resource_freqdeck.copy()
    d["development_listdeck"] = self.development_listdeck.copy()
    d["buildings_by_color"] = {
        color: defaultdict(list, {kind: list(ids) for kind, ids in by_kind.items()})
        for color, by_kind in self.buildings_by_color.items()
    }
    d["action_records"] = self.action_records.copy()
    d["discard_counts"] = self.discard_counts.copy()
    if self.dice is not None:
        d["dice"] = self.dice.copy()
    return c


print(f"fast       {per(lambda g: fast(g.state)):6.2f} us")
import gc

gc.disable()
print(f"no-gc game.copy  {per(lambda g: g.copy()):6.2f} us")
print(f"no-gc state.copy {per(lambda g: g.state.copy()):6.2f} us")
gc.enable()
print(f"empty      {per(lambda g: None):6.2f} us")
avg = sum(len(g.state.action_records) for g in snaps) / len(snaps)
print(f"  mean action_records {avg:.0f}")
