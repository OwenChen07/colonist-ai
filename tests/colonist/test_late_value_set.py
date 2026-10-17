from catanatron.colonist.late_value_set import _slice
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
