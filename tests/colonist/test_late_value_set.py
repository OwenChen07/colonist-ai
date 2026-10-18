import math

from catanatron.colonist.late_value_set import _half_width, _slice
from catanatron.game import Game
from catanatron.models.player import Color, RandomPlayer


def test_slice_by_what_the_person_holds():
    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=3)
    s = game.state
    p = s.player_state
    i = s.color_to_index[Color.BLUE]
    assert _slice(s, Color.RED) == ""
    p[f"P{i}_KNIGHT_IN_HAND"] = 1
    assert _slice(s, Color.RED) == "dev"
    p[f"P{i}_HAS_ARMY"] = True
    assert _slice(s, Color.RED) == "army_dev"
    # The bot's own cards don't count.
    assert _slice(s, Color.BLUE) == ""


def test_half_width_counts_games_not_positions():
    # One position per game: the usual standard error (with k/(k-1)).
    one = [(float(g % 2), g) for g in range(100)]
    assert math.isclose(_half_width(one), 1.96 * math.sqrt(0.25 / 99), rel_tol=1e-9)
    # Ten copies of each position in its own game: same games, same width.
    ten = [(x, g) for x, g in one for _ in range(10)]
    assert math.isclose(_half_width(ten), _half_width(one), rel_tol=1e-9)
    assert math.isnan(_half_width([(1.0, 0), (0.0, 0)]))
