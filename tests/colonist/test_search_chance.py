"""Chance nodes in the bots' look-ahead (players/tree_search_utils.py).

Upstream built one branch per outcome but never applied the outcome: every
roll branch rolled fresh random dice, and every development card branch drew
the real top card of the deck.
"""

import random

import pytest

from catanatron import Color, Game
from catanatron.models.actions import generate_playable_actions
from catanatron.models.enums import ActionType
from catanatron.models.player import RandomPlayer
from catanatron.players.tree_search_utils import expand_spectrum
from catanatron.state_functions import player_key


def game_at(action_type, dice, seed=3):
    random.seed(seed)
    game = Game(
        [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed, dice=dice
    )
    while not any(a.action_type == ActionType.ROLL for a in game.playable_actions):
        game.play_tick()
    if action_type == ActionType.BUY_DEVELOPMENT_CARD:
        game.execute(
            [a for a in game.playable_actions if a.action_type == ActionType.ROLL][0]
        )
        key = player_key(game.state, game.state.current_color())
        for r in ("SHEEP", "WHEAT", "ORE"):
            game.state.player_state[f"{key}_{r}_IN_HAND"] += 1
        game.playable_actions = generate_playable_actions(game.state)
    action = next(a for a in game.playable_actions if a.action_type == action_type)
    return game, action


@pytest.mark.parametrize("dice", ["random", "balanced"])
def test_roll_branches_produce_their_totals(dice):
    game, roll = game_at(ActionType.ROLL, dice)
    branches = expand_spectrum(game, [roll])[roll]
    totals = [sum(g.state.action_records[-1].result) for g, _ in branches]
    assert totals == sorted(set(totals))  # one branch per total, in order
    assert sum(p for _, p in branches) == pytest.approx(1)
    if dice == "random":
        assert totals == list(range(2, 13))
    else:
        odds = game.state.dice.odds(roll.color)
        assert [p for _, p in branches] == pytest.approx([o for o in odds if o > 0])


def test_dev_card_branches_draw_different_cards():
    game, buy = game_at(ActionType.BUY_DEVELOPMENT_CARD, "random")
    branches = expand_spectrum(game, [buy])[buy]
    drawn = {g.state.action_records[-1].result for g, _ in branches}
    assert len(drawn) == len(branches) > 1


def _one_point_from_winning(seed=3):
    """A colonist 1v1 position where the player to move can build, set one
    point short of winning, so the build wins."""
    import random as _random

    from catanatron.colonist.rules import new_game
    from catanatron.models.enums import ActionType
    from catanatron.models.player import Color, Player
    from catanatron.state_functions import player_key

    rng = _random.Random(seed)
    game = new_game([Player(Color.RED), Player(Color.BLUE)], seed=seed)
    builds = (ActionType.BUILD_CITY, ActionType.BUILD_SETTLEMENT)
    for _ in range(5000):
        state = game.state
        if not state.is_initial_build_phase and any(
            a.action_type in builds for a in game.playable_actions
        ):
            key = player_key(state, state.current_color()) + "_"
            state.player_state[key + "ACTUAL_VICTORY_POINTS"] = game.vps_to_win - 1
            state.player_state[key + "VICTORY_POINTS"] = game.vps_to_win - 1
            return game
        game.execute(rng.choice(game.playable_actions))
    raise AssertionError("no build position found")


def test_the_search_always_takes_an_immediate_win():
    from catanatron.colonist.graph import random_graph_net
    from catanatron.colonist.mcts import Decision, Search, immediate_win
    from catanatron.models.enums import ActionType

    game = _one_point_from_winning()
    color = game.state.current_color()
    root = Decision(game, color, list(game.playable_actions))
    win = immediate_win(root)
    assert win is not None
    assert root.actions[win].action_type in (
        ActionType.BUILD_CITY,
        ActionType.BUILD_SETTLEMENT,
    )
    search = Search(random_graph_net(seed=1), simulations=16)
    action, played = search.run(game)
    after = game.copy()
    after.execute(action)
    assert after.winning_color() == color
    pi, v = search.root_targets(played)
    assert pi.sum() == 1.0 and pi[played.immediate_win] == 1.0 and v == 1.0
    # Off: the plain search, which may or may not find it.
    off = Search(random_graph_net(seed=1), simulations=16, take_immediate_wins=False)
    off.run(game)


def test_target_scale_softens_only_the_target():
    """E59: target_scale changes root_targets' policy, not the search."""
    import numpy as np

    from catanatron.colonist.graph import random_graph_net
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.rules import new_game
    from catanatron.models.player import Player

    game = new_game([Player(Color.RED), Player(Color.BLUE)], seed=5)
    while len(game.playable_actions) < 3 or game.state.is_initial_build_phase:
        game.execute(game.playable_actions[0])
    net = random_graph_net(seed=2)
    runs = {}
    for scale in (None, 1.0, 0.1):
        search = Search(net, 32, rng=random.Random(0), target_scale=scale)
        action, root = search.run(game)
        runs[scale] = (action, *search.root_targets(root), search, root)
    assert runs[None][0] == runs[1.0][0] == runs[0.1][0]
    assert np.array_equal(runs[None][1], runs[1.0][1])
    hard, soft = runs[1.0][1], runs[0.1][1]
    entropy = lambda p: -(p[p > 0] * np.log(p[p > 0])).sum()
    assert entropy(soft) > entropy(hard)
    search, root = runs[1.0][3:]
    assert np.allclose(search.root_targets(root, scale=0.1)[0], soft)


def test_no_immediate_win_far_from_the_end():
    from catanatron.colonist.mcts import Decision, immediate_win
    from catanatron.colonist.rules import new_game
    from catanatron.models.player import Color, Player

    game = new_game([Player(Color.RED), Player(Color.BLUE)], seed=4)
    root = Decision(game, game.state.current_color(), list(game.playable_actions))
    assert immediate_win(root) is None
