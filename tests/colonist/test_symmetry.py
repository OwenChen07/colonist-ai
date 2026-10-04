"""The board symmetries used to augment training data."""

import random
from collections import defaultdict

import numpy as np
import pytest

from catanatron.colonist import encode as E
from catanatron.colonist.symmetry import symmetries
from catanatron.colonist.rules import new_game
from catanatron.models.enums import CITY, ROAD, SETTLEMENT
from catanatron.models.map import CatanMap
from catanatron.models.player import Color
from catanatron.models.tiles import LandTile
from catanatron.players.weighted_random import WeightedRandomPlayer


def _inverse(image):
    inv = np.empty_like(image)
    inv[image] = np.arange(len(image))
    return inv


def transformed(game, sym):
    """The same position with the board physically rotated/flipped: each
    land tile's resource and number moved to its image, buildings and roads
    to their images. Ports stay where they are (they sit on water tiles,
    which these maps don't move), so port inputs are not compared."""
    old = game.state.board.map
    cache = E._map_cache(old)
    coordinate_of = {i: c for c, i in cache.tile_of_coordinate.items()}
    tile_inv = _inverse(sym.tile_image)
    tiles = dict(old.tiles)
    for c, tile in old.land_tiles.items():
        source = old.land_tiles[coordinate_of[tile_inv[cache.tile_of_coordinate[c]]]]
        tiles[c] = LandTile(
            tile.id, source.resource, source.number, tile.nodes, tile.edges
        )
    g = game.copy()
    board = g.state.board
    board.map = CatanMap.from_tiles(tiles)
    node = lambda n: int(sym.node_image[n])
    edge = lambda e: E.EDGES[sym.edge_image[E.EDGE_INDEX[e]]]
    board.robber_coordinate = coordinate_of[
        sym.tile_image[cache.tile_of_coordinate[board.robber_coordinate]]
    ]
    for color, by_kind in game.state.buildings_by_color.items():
        new = defaultdict(list)
        new[SETTLEMENT] = [node(n) for n in by_kind[SETTLEMENT]]
        new[CITY] = [node(n) for n in by_kind[CITY]]
        new[ROAD] = [edge(e) for e in by_kind[ROAD]]
        g.state.buildings_by_color[color] = new
    return g


def _port_columns():
    start = E.NUM_TILES * 7 + E.NUM_NODES * 5  # corner ports
    cols = list(range(start, start + E.NUM_NODES * 6))
    # per-player "ports owned" features
    me = E.STATIC_SIZE + E.NUM_TILES + E.NUM_NODES * 4 + E.NUM_EDGES * 2
    for start in (me + 29, me + E.PLAYER_SIZE + E.MY_EXTRA + 29):
        cols += range(start, start + 6)
    return np.asarray(cols)


@pytest.fixture(scope="module")
def positions():
    random.seed(4)
    game = new_game(
        [WeightedRandomPlayer(Color.RED), WeightedRandomPlayer(Color.BLUE)], seed=4
    )
    out = []
    while game.winning_color() is None and game.state.num_turns < 300:
        if game.state.num_turns % 7 == 0:
            out.append(game.copy())
        game.play_tick()
    return out[::5]


def test_twelve_distinct_symmetries_identity_first():
    syms = symmetries()
    assert len(syms) == 12
    assert len({tuple(s.node_image) for s in syms}) == 12
    assert (syms[0].inputs == np.arange(E.INPUT_SIZE)).all()
    assert (syms[0].move_image == np.arange(E.NUM_ACTIONS)).all()
    for s in syms:
        assert sorted(s.inputs) == list(range(E.INPUT_SIZE))
        assert sorted(s.move_image) == list(range(E.NUM_ACTIONS))


def test_inputs_match_a_physically_transformed_position(positions):
    keep = np.setdiff1d(np.arange(E.INPUT_SIZE), _port_columns())
    for game in positions:
        color = game.state.current_color()
        x = E.encode_flat(game, color)
        for sym in symmetries()[1:]:
            expected = E.encode_flat(transformed(game, sym), color)
            assert np.allclose(x[sym.inputs][keep], expected[keep])


def test_legal_moves_match_a_physically_transformed_position(positions):
    for game in positions:
        legal = np.zeros(E.NUM_ACTIONS, bool)
        legal[E.legal_indices(game.playable_actions)] = True
        for sym in symmetries()[1:]:
            from catanatron.models.actions import generate_playable_actions

            g = transformed(game, sym)
            g.state.board.buildable_edges_cache = {}
            g.state.board.player_port_resources_cache = {}
            _rebuild_board(g)
            moves = [
                a
                for a in generate_playable_actions(g.state)
                if a.action_type.value != "MARITIME_TRADE"  # ports didn't move
            ]
            expected = np.zeros(E.NUM_ACTIONS, bool)
            expected[E.legal_indices(moves)] = True
            got = legal[sym.moves]
            trade = np.asarray(
                [t.value == "MARITIME_TRADE" for t, _ in E.action_table()]
            )
            assert (got[~trade] == expected[~trade]).all()


def _rebuild_board(g):
    """Rebuild the board's own indexes from buildings_by_color."""
    from catanatron.models.board import Board

    old = g.state.board
    board = Board(old.map)
    board.robber_coordinate = old.robber_coordinate
    # Replay settlements first (as initial placements, so connectivity rules
    # don't apply), then roads, then upgrade cities.
    for color, by_kind in g.state.buildings_by_color.items():
        for n in by_kind[SETTLEMENT] + by_kind[CITY]:
            board.build_settlement(color, n, initial_build_phase=True)
    for color, by_kind in g.state.buildings_by_color.items():
        for e in by_kind[ROAD]:
            board.build_road(color, e)
        for n in by_kind[CITY]:
            board.build_city(color, n)
    g.state.board = board


def test_training_augmentation_keeps_targets_legal(positions):
    torch = pytest.importorskip("torch")
    from catanatron.colonist.train import Augment

    xs, legals, actions = [], [], []
    for game in positions:
        color = game.state.current_color()
        xs.append(E.encode_flat(game, color))
        mask = np.zeros(E.NUM_ACTIONS, bool)
        idx = E.legal_indices(game.playable_actions)
        mask[idx] = True
        legals.append(mask)
        actions.append(idx[-1])
    x = torch.tensor(np.stack(xs))
    legal = torch.tensor(np.stack(legals))
    action = torch.tensor(actions)
    pi = torch.nn.functional.one_hot(action, E.NUM_ACTIONS).float()
    augment = Augment("cpu", torch.Generator().manual_seed(0))
    x2, legal2, action2, pi2, _ = augment(x, legal, action, pi, None)
    assert legal2[torch.arange(len(action2)), action2].all()
    assert (pi2.argmax(1) == action2).all()
    assert torch.equal(legal2.sum(1), legal.sum(1))
    assert not torch.equal(x2, x)
