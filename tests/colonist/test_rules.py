from catanatron import Color
from catanatron.colonist.rules import COLONIST_1V1, new_game
from catanatron.models.actions import robber_possibilities
from catanatron.models.player import RandomPlayer
from catanatron.state_functions import player_key


def two_players():
    return [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)]


def test_new_game_uses_colonist_rules():
    game = new_game(two_players(), seed=1)
    assert game.vps_to_win == 15
    assert game.state.discard_limit == 9
    assert game.state.friendly_robber is True
    assert game.state.dice is not None


def test_rules_can_be_overridden():
    game = new_game(two_players(), seed=1, dice="random", vps_to_win=10)
    assert game.state.dice is None
    assert game.vps_to_win == 10
    assert COLONIST_1V1["dice"] == "balanced"  # the defaults are untouched


def play_through_setup(game):
    while game.state.is_initial_build_phase:
        game.play_tick()


def blue_tiles(game):
    s = game.state
    blue_nodes = set(s.buildings_by_color[Color.BLUE]["SETTLEMENT"])
    return {
        coordinate
        for coordinate, tile in s.board.map.land_tiles.items()
        if blue_nodes & set(tile.nodes.values())
        and coordinate != s.board.robber_coordinate
    }


def test_friendly_robber_counts_visible_points_only():
    game = new_game(two_players(), seed=3)
    play_through_setup(game)
    s = game.state
    blue = player_key(s, Color.BLUE)
    s.player_state[f"{blue}_VICTORY_POINTS"] = 2

    # Two visible points: BLUE is protected.
    protected = {a.value[0] for a in robber_possibilities(s, Color.RED)}
    assert not (protected & blue_tiles(game))

    # A hidden victory point card raises actual points to 3 but must not lift
    # the protection (or the legal moves would reveal the card).
    s.player_state[f"{blue}_ACTUAL_VICTORY_POINTS"] = 3
    s.player_state[f"{blue}_VICTORY_POINT_IN_HAND"] = 1
    still_protected = {a.value[0] for a in robber_possibilities(s, Color.RED)}
    assert not (still_protected & blue_tiles(game))

    # Three visible points: no longer protected.
    s.player_state[f"{blue}_VICTORY_POINTS"] = 3
    exposed = {a.value[0] for a in robber_possibilities(s, Color.RED)}
    assert exposed & blue_tiles(game)
