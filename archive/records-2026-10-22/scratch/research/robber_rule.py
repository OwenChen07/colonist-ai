"""Does round 73's raw policy already aim the robber like rehex's rule?
Rule: tile score = opponent's pips on it - own pips (cities x2); never own-hex.
Self-play, moves sampled from the raw policy; at each robber decision compare
the net's highest-probability tile with the rule's best tiles."""

import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net
from catanatron.colonist.selfplay import fresh_game
from catanatron.colonist.mcts import TURNS_LIMIT
from catanatron.models.enums import ActionType

R73 = "/home/owen/colonist-ai/runs/g1/nets/round_073.npz"
games = int(sys.argv[1]) if len(sys.argv) > 1 else 200


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def tile_scores(game, me):
    board = game.state.board
    out = {}
    for coord, tile in board.map.land_tiles.items():
        mine = theirs = 0
        for nid in tile.nodes.values():
            b = board.buildings.get(nid)
            if b is None:
                continue
            w = pips(tile.number) * (2 if b[1] == "CITY" else 1)
            if b[0] == me:
                mine += w
            else:
                theirs += w
        out[coord] = (theirs - mine, mine > 0)
    return out


net = Net.load(R73)
n = agree = own_hex_net = own_hex_avoidable = 0
mass = []
regret = []
for seed in range(7_000_000, 7_000_000 + games):
    game = fresh_game(seed)
    rng = random.Random(seed)
    while game.winning_color() is None and game.state.num_turns < TURNS_LIMIT:
        actions = game.playable_actions
        if len(actions) == 1:
            game.execute(actions[0])
            continue
        me = game.state.current_color()
        probs, _ = net.evaluate(game, me, E.legal_indices(actions))
        if actions[0].action_type == ActionType.MOVE_ROBBER:
            sc = tile_scores(game, me)
            per = {}
            for a, p in zip(actions, probs):
                per[a.value[0]] = per.get(a.value[0], 0.0) + p
            best = max(sc[c][0] for c in per)
            top = max(per, key=per.get)
            n += 1
            agree += sc[top][0] == best
            regret.append(best - sc[top][0])
            mass.append(sum(p for c, p in per.items() if sc[c][0] == best))
            if any(not sc[c][1] for c in per):
                own_hex_avoidable += 1
                own_hex_net += sc[top][1]
        pick = rng.choices(range(len(actions)), weights=probs)[0]
        game.execute(actions[pick])

regret = np.array(regret)
se = (agree / n * (1 - agree / n) / n) ** 0.5
print(f"{games} games, {n} robber moves")
print(
    f"net's top tile is a rule-best tile: {agree}/{n} = {agree/n:.1%} +- {1.96*se:.1%}"
)
print(f"mean policy mass on rule-best tiles: {np.mean(mass):.1%}")
print(
    f"pip shortfall of net's top vs rule best: mean {regret.mean():.2f}, "
    f">=2 pips {np.mean(regret >= 2):.1%}, >=4 pips {np.mean(regret >= 4):.1%}"
)
print(
    f"net's top tile on its own hex (when avoidable): {own_hex_net}/{own_hex_avoidable}"
)
