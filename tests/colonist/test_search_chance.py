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
