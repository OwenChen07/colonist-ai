from catanatron.colonist.robber_policy import hex_pips, robber_root_actions
from catanatron.game import Game
from catanatron.models.enums import CITY, SETTLEMENT, Action, ActionType
from catanatron.models.player import Color, RandomPlayer


def board_with(own_x, opp_x_kind, opp_y):
    """A game where RED moves the robber; hex X (6) holds RED's settlement if
    own_x and BLUE's building of opp_x_kind, hex Y (8) BLUE's settlement if
    opp_y, hex Z (2) nothing. X, Y and Z share no corner."""
    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=1)
    state = game.state
    land = [
        (c, t) for c, t in state.board.map.land_tiles.items() if t.number is not None
    ]
    picked = []
    for coordinate, tile in land:
        nodes = set(tile.nodes.values())
        if all(nodes.isdisjoint(t.nodes.values()) for _, t in picked):
            picked.append((coordinate, tile))
        if len(picked) == 3:
            break
    (x, tx), (y, ty), (z, tz) = picked
    tx.number, ty.number, tz.number = 6, 8, 2
    buildings = state.board.buildings
    buildings.clear()
    nx = list(tx.nodes.values())
    if own_x:
        buildings[nx[0]] = (Color.RED, SETTLEMENT)
    if opp_x_kind:
        buildings[nx[3]] = (Color.BLUE, opp_x_kind)
    if opp_y:
        buildings[list(ty.nodes.values())[0]] = (Color.BLUE, SETTLEMENT)
    state.current_player_index = state.colors.index(Color.RED)
    moves = [
        Action(Color.RED, ActionType.MOVE_ROBBER, (x, Color.BLUE)),
        Action(Color.RED, ActionType.MOVE_ROBBER, (y, Color.BLUE)),
        Action(Color.RED, ActionType.MOVE_ROBBER, (z, None)),
    ]
    return game, (x, y, z), moves


def test_hex_pips_doubles_cities():
    game, (x, y, z), _ = board_with(True, CITY, True)
    assert hex_pips(game.state, x) == {Color.RED: 5, Color.BLUE: 10}
    assert hex_pips(game.state, y) == {Color.BLUE: 5}
    assert hex_pips(game.state, z) == {}


def test_own_hex_dropped_when_a_clean_hex_blocks_as_much():
    game, (x, y, z), moves = board_with(True, SETTLEMENT, True)
    kept = robber_root_actions(game, moves)
    assert [a.value[0] for a in kept] == [y, z]


def test_own_hex_kept_when_it_blocks_more_of_the_opponent():
    game, _, moves = board_with(True, CITY, True)
    assert robber_root_actions(game, moves) == moves


def test_no_clean_hex_keeps_every_move():
    game, _, moves = board_with(True, SETTLEMENT, False)
    assert robber_root_actions(game, moves) == moves


def test_other_prompts_untouched():
    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=1)
    actions = list(game.playable_actions)
    assert robber_root_actions(game, actions) == actions
