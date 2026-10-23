"""Raw round 73 vs raw round 73 whose robber tile follows rehex's rule (victim
and everything else from the net). Seats alternate by seed; moves sampled."""

import random
import sys

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net
from catanatron.colonist.selfplay import fresh_game
from catanatron.colonist.mcts import TURNS_LIMIT
from catanatron.models.enums import ActionType
from catanatron.models.player import Color

sys.path.insert(0, "/home/owen/colonist-ai/data/orchestrator/scratch/research")
from robber_rule_lib import tile_scores  # noqa: E402

R73 = "/home/owen/colonist-ai/runs/g1/nets/round_073.npz"
games = int(sys.argv[1])
net = Net.load(R73)
wins = losses = 0
for seed in range(8_000_000, 8_000_000 + games):
    game = fresh_game(seed)
    rng = random.Random(seed)
    rule_color = Color.RED if seed % 2 == 0 else Color.BLUE
    while game.winning_color() is None and game.state.num_turns < TURNS_LIMIT:
        actions = game.playable_actions
        if len(actions) == 1:
            game.execute(actions[0])
            continue
        me = game.state.current_color()
        probs, _ = net.evaluate(game, me, E.legal_indices(actions))
        probs = list(probs)
        if me == rule_color and actions[0].action_type == ActionType.MOVE_ROBBER:
            sc = tile_scores(game, me)
            best = max(sc[a.value[0]][0] for a in actions)
            probs = [
                p if sc[a.value[0]][0] == best else 0.0 for a, p in zip(actions, probs)
            ]
            if sum(probs) == 0:
                probs = [1.0 if sc[a.value[0]][0] == best else 0.0 for a in actions]
        pick = rng.choices(range(len(actions)), weights=probs)[0]
        game.execute(actions[pick])
    w = game.winning_color()
    wins += w == rule_color
    losses += w is not None and w != rule_color
n = wins + losses
p = wins / n
print(
    f"rule-robber r73 vs r73: {wins}-{losses} = {p:.1%} +- {1.96*(p*(1-p)/n)**0.5:.1%} "
    f"({games - n} unfinished)"
)
