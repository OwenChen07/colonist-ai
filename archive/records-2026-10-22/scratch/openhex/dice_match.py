"""Does the OpenHex dice input cost strength? A match on plain dice between
round 73 seeing the dice as the OpenHex client feeds them ("live": balanced
model that switches to random at the first impossible roll) and round 73
seeing a balanced model that never switches ("shadow": every roll forces a
card of that total out of its deck, so the input looks like training). Both
searches use the live model's odds at chance nodes, as the client does.

Usage: dice_match.py PAIRS SIMS [FIRST_SEED]   (one process; appends to
data/orchestrator/scratch/openhex/dice_match.jsonl)
"""

import json
import random
import sys
import time

from catanatron.colonist import encode as E
from catanatron.colonist.evaluate import _seat
from catanatron.colonist.mcts import NetPlayer
from catanatron.colonist.rules import new_game
from catanatron.models.dice import BalancedDice, roll_plain_dice
from catanatron.models.player import Color

NET = "runs/g1/nets/round_073.npz"
OUT = "data/orchestrator/scratch/openhex/dice_match.jsonl"
VIEW = ["live"]


def forced_observe(d, who, pair):
    d._sevens_include(who)
    d._reshuffle_if_needed()
    i = pair[0] + pair[1] - 2
    if d.deck[i]:
        d._draw(i, pair if pair in d.deck[i] else d.deck[i][0])
    d._record(who, pair)


class DualDice:
    """Plain dice with two models of them; the encoder sees VIEW's."""

    def __init__(self, live=None, shadow=None):
        self.live = live or BalancedDice(2)
        self.shadow = shadow or BalancedDice(2)

    def view(self):
        return self.shadow if VIEW[0] == "shadow" else self.live

    def __getattr__(self, name):  # mode, deck, recent, _cache ... for encode
        return getattr(self.view(), name)

    def odds(self, color):  # chance nodes: always the live model
        return self.live.odds(color)

    def example_pair(self, total):
        return self.live.example_pair(total)

    def observe(self, color, pair):
        pair = (int(pair[0]), int(pair[1]))
        self.live.observe(color, pair)
        forced_observe(self.shadow, color, pair)

    def roll(self, rng, color):
        pair = roll_plain_dice(rng)
        self.observe(color, pair)
        return pair

    def copy(self):
        return DualDice(self.live.copy(), self.shadow.copy())


_orig_features = E._dice_features
E._dice_features = lambda d, me, them: _orig_features(
    d.view() if isinstance(d, DualDice) else d, me, them
)


class ViewPlayer(NetPlayer):
    def __init__(self, color, sims, view, seed):
        super().__init__(color, NetPlayer.Params(NET, sims, seed))
        self.view = view

    def decide(self, game, playable_actions):
        VIEW[0] = self.view
        E._dice_memo.clear()  # its key has no view; live and shadow can collide
        return super().decide(game, playable_actions)


def play(seed, shadow_first, sims):
    stream = seed * 2 + int(shadow_first)
    random.seed(stream)
    shadow = ViewPlayer(Color.RED, sims, "shadow", stream)
    live = ViewPlayer(Color.BLUE, sims, "live", stream + 1)
    game = new_game([shadow, live], seed=seed)
    _seat(game, [shadow, live] if shadow_first else [live, shadow])
    game.state.dice = DualDice()
    game.random.seed(stream)
    t = time.time()
    winner = game.play()
    return {
        "seed": seed,
        "shadow_first": shadow_first,
        "sims": sims,
        "shadow_score": 0.5 if winner is None else float(winner == Color.RED),
        "turns": game.state.num_turns,
        "broke": game.state.dice.live.mode == "random",
        "seconds": round(time.time() - t, 1),
    }


pairs, sims = int(sys.argv[1]), int(sys.argv[2])
first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
for seed in range(first, first + pairs):
    for shadow_first in (True, False):
        row = play(seed, shadow_first, sims)
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(row, flush=True)
