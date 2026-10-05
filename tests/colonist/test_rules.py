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


def red_tiles(game):
    s = game.state
    red_nodes = set(s.buildings_by_color[Color.RED]["SETTLEMENT"])
    return {
        coordinate
        for coordinate, tile in s.board.map.land_tiles.items()
        if red_nodes & set(tile.nodes.values())
        and coordinate != s.board.robber_coordinate
    }


def test_friendly_robber_protects_the_mover_too():
    """colonist's friendly robber protects every player with 2 or fewer visible
    points, including the one moving the robber (seen in a live game)."""
    game = new_game(two_players(), seed=3)
    play_through_setup(game)
    s = game.state
    red = player_key(s, Color.RED)
    s.player_state[f"{red}_VICTORY_POINTS"] = 2
    own = red_tiles(game)
    assert own
    moves = {a.value[0] for a in robber_possibilities(s, Color.RED)}
    assert not (moves & own)

    # At 3 points (and with the opponent at 3, so shared tiles aren't
    # protected on their account), the mover's tiles are open again.
    s.player_state[f"{red}_VICTORY_POINTS"] = 3
    s.player_state[f"{player_key(s, Color.BLUE)}_VICTORY_POINTS"] = 3
    moves = {a.value[0] for a in robber_possibilities(s, Color.RED)}
    assert own <= moves


def test_road_into_an_opponent_settlement_counts_but_stops_there():
    """An opponent's settlement breaks a road: the road leading into it still
    counts toward longest road, but nothing past it does. (Seen live: colonist
    counted 6 where the simulator counted 5 and gave longest road away.)"""
    from catanatron.models.board import Board, longest_acyclic_path
    from catanatron.models.enums import NodeRef

    board = Board()
    tile = board.map.land_tiles[(0, 0, 0)]
    refs = (NodeRef.NORTH, NodeRef.NORTHEAST, NodeRef.SOUTHEAST, NodeRef.SOUTH)
    ring = [tile.nodes[r] for r in refs]
    board.build_settlement(Color.RED, ring[0], initial_build_phase=True)
    for a, b in zip(ring, ring[1:]):
        board.build_road(Color.RED, (a, b))

    def red_longest():
        return max(
            len(longest_acyclic_path(board, component, Color.RED))
            for component in board.connected_components[Color.RED]
        )

    assert red_longest() == 3
    # BLUE's settlement at the far end, as build_settlement leaves it (the
    # node is dropped from RED's road component): the road into it still
    # counts...
    board.buildings[ring[-1]] = (Color.BLUE, "SETTLEMENT")
    for component in board.connected_components[Color.RED]:
        component.discard(ring[-1])
    assert red_longest() == 3
    # ...but a settlement in the middle splits it (no counting through).
    del board.buildings[ring[-1]]
    for component in board.connected_components[Color.RED]:
        component.add(ring[-1])
    board.buildings[ring[1]] = (Color.BLUE, "SETTLEMENT")
    assert red_longest() == 2
