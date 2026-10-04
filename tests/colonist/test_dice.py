"""Balanced dice against colonist's own controller.

fixtures/dice_reference.json comes from a line-for-line port of colonist's
DiceControllerBalanced (see make_dice_fixture.js), driven by the xorshift
generator below. Fed the same stream, the Python model must draw the same
rolls and use the same weights.
"""

import json
import os
import random

import pytest

from catanatron import Color, Game
from catanatron.models.dice import BalancedDice
from catanatron.models.player import RandomPlayer
from catanatron.serialization import state_from_json, state_to_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "dice_reference.json")
with open(FIXTURE) as f:
    REFERENCE = json.load(f)["games"]


class XorShift:
    """The fixture's generator: the JS xorshift32, as a .random() source."""

    def __init__(self, seed):
        self.s = (seed & 0xFFFFFFFF) or 1

    def random(self):
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s
        return s / 4294967296


def normalized(w):
    total = sum(w)
    return [x / total for x in w]


@pytest.mark.parametrize("game", REFERENCE, ids=lambda g: f"seed{g['seed']}")
def test_rolls_and_weights_match_colonist(game):
    dice = BalancedDice(len(game["players"]))
    rng = XorShift(game["seed"])
    for n, (player, d1, d2, weights) in enumerate(game["rolls"]):
        expected = normalized(weights)
        assert dice.odds(player) == pytest.approx(expected, abs=1e-9), f"roll {n}"
        assert dice.roll(rng, player) == (d1, d2), f"roll {n}"
    assert dice.mode == "balanced"


@pytest.mark.parametrize("game", REFERENCE[:5], ids=lambda g: f"seed{g['seed']}")
def test_observer_follows_colonist(game):
    """Watching the rolls (as a replay or the tracker does) gives the same odds."""
    dice = BalancedDice(len(game["players"]))
    for n, (player, d1, d2, weights) in enumerate(game["rolls"]):
        assert dice.odds(player) == pytest.approx(normalized(weights), abs=1e-9), n
        dice.observe(player, (d1, d2))
    assert dice.mode == "balanced"


def test_impossible_roll_switches_to_random():
    dice = BalancedDice(2)
    for _ in range(3):  # three 12s in a row: the only 12 card is gone after the first
        dice.observe("RED", (6, 6))
    assert dice.mode == "random"
    assert dice.odds("RED")[10] == pytest.approx(1 / 36)


def test_recent_rolls_block_a_total():
    dice = BalancedDice(2)
    for pair in [(1, 3), (3, 1), (2, 2)]:  # every 4 rolled, three times in the last 5
        dice.observe("RED", pair)
    assert dice.odds("BLUE")[4 - 2] == 0


def test_seven_streak_lowers_own_sevens():
    dice = BalancedDice(2)
    dice.observe("RED", (3, 4))
    dice.observe("BLUE", (1, 2))
    # One 7 between two players: too few for the share rule, so share is 1.
    assert dice.seven_multiplier("RED") == pytest.approx(1 - 0.4)
    assert dice.seven_multiplier("BLUE") == pytest.approx(1 + 0.4)
    dice.observe("RED", (2, 5))
    # RED has both 7s and a run of 2: share 0, streak -0.8 -> clamped to 0.
    assert dice.seven_multiplier("RED") == 0
    # BLUE: share 2, streak +0.8 -> clamped to 2.
    assert dice.seven_multiplier("BLUE") == 2


def test_copy_is_independent():
    dice = BalancedDice(2)
    dice.observe("RED", (3, 4))
    copy = dice.copy()
    copy.observe("BLUE", (6, 6))
    assert dice.cards_left == 35 and copy.cards_left == 34
    assert dice.recent_count[10] == 0


def test_example_pair_is_accepted():
    dice = BalancedDice(2)
    dice.observe("RED", (1, 1))
    for total in range(3, 13):
        copy = dice.copy()
        copy.observe("BLUE", copy.example_pair(total))
        assert copy.mode == "balanced", total


def test_game_with_balanced_dice_plays_and_round_trips():
    random.seed(11)
    game = Game(
        [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=5, dice="balanced"
    )
    for _ in range(300):
        if game.winning_color():
            break
        game.play_tick()
    assert game.state.dice.mode == "balanced"
    assert game.state.dice.cards_left < 36

    players = [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)]
    loaded = state_from_json(state_to_json(game), players)
    assert loaded.state.dice.to_json() == game.state.dice.to_json()


def test_unknown_dice_kind_is_rejected():
    with pytest.raises(ValueError):
        Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], dice="loaded")
