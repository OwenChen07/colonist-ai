"""What the network sees, and the moves it chooses from (PLAN.md, Phase 2).

A position is encoded from the point of view of one player ("me"; the other
is "them"), as two float32 vectors:

  - static: the board, which never changes during a game, so it is built once
    per map and shared by every position of the game. Tiles (resource and
    number odds) and, for each of the 54 corners, what it produces and which
    port it opens. The per-corner part saves the network from learning which
    tiles touch which corner.
  - dynamic: everything else. Robber, buildings and roads, both players' hands
    and progress, the bank, the turn's phase, and the balanced-dice state.

Only what "me" could know goes in. Their resource hand is included: in 1v1
the log shows every card that changes hands (both players see a steal), so
colonist players and the tracker can follow it exactly. Their unplayed
development cards are only a count, and their hidden victory points are left
out (their visible points are in).

Moves: the 313 actions a 2-player game can contain, without trades between
players (the AI never makes them). A robber move names its victim as "them",
so the same index means the same move from either seat.
"""

import weakref
from functools import lru_cache

import numpy as np

from catanatron.models.board import get_edges
from catanatron.models.dice import MIN_CARDS_BEFORE_RESHUFFLE
from catanatron.models.enums import (
    DEVELOPMENT_CARDS,
    RESOURCES,
    SETTLEMENT,
    CITY,
    ROAD,
    VICTORY_POINT,
    ActionType,
)
from catanatron.models.map import build_map, number_probability

NUM_TILES = 19
NUM_NODES = 54
EDGES = tuple(tuple(sorted(e)) for e in get_edges())
EDGE_INDEX = {e: i for i, e in enumerate(EDGES)}
EDGE_INDEX.update({(b, a): i for (a, b), i in list(EDGE_INDEX.items())})
NUM_EDGES = len(EDGES)  # 72
PLAYABLE_DEVS = [c for c in DEVELOPMENT_CARDS if c != VICTORY_POINT]
PORT_KINDS = RESOURCES + [None]  # None: 3:1
_SCALE_PROBA = 36 / 5  # a single 6 or 8 becomes 1

# ===== moves
THEM = "THEM"  # robber victim, relative to the player moving


@lru_cache(None)
def action_table():
    """The 313 moves, as (ActionType, value) keys, in a fixed order. The same
    moves as the gym's action space for two players (which needs gymnasium),
    less trades between players and with the robber's victim relative."""
    R = RESOURCES
    keys = [
        (ActionType.ROLL, None),
        *[(ActionType.DISCARD_RESOURCE, r) for r in R],
        *[(ActionType.BUILD_ROAD, e) for e in EDGES],
        *[(ActionType.BUILD_SETTLEMENT, n) for n in range(NUM_NODES)],
        *[(ActionType.BUILD_CITY, n) for n in range(NUM_NODES)],
        (ActionType.BUY_DEVELOPMENT_CARD, None),
        (ActionType.PLAY_KNIGHT_CARD, None),
        *[
            (ActionType.PLAY_YEAR_OF_PLENTY, (R[i], R[j]))
            for i in range(5)
            for j in range(i, 5)
        ],
        *[(ActionType.PLAY_YEAR_OF_PLENTY, (r,)) for r in R],
        (ActionType.PLAY_ROAD_BUILDING, None),
        *[(ActionType.PLAY_MONOPOLY, r) for r in R],
        *[
            (ActionType.MOVE_ROBBER, (c, victim))
            for c in build_map("BASE").land_tiles
            for victim in (None, THEM)
        ],
        *[
            (ActionType.MARITIME_TRADE, tuple(4 * [i] + [j]))
            for i in R
            for j in R
            if i != j
        ],
        *[
            (ActionType.MARITIME_TRADE, tuple(3 * [i] + [None, j]))
            for i in R
            for j in R
            if i != j
        ],
        *[
            (ActionType.MARITIME_TRADE, tuple(2 * [i] + [None, None, j]))
            for i in R
            for j in R
            if i != j
        ],
        (ActionType.END_TURN, None),
    ]
    return tuple(sorted(keys, key=str))


@lru_cache(None)
def _action_index():
    return {key: i for i, key in enumerate(action_table())}


NUM_ACTIONS = 313


def action_key(action):
    value = action.value
    if action.action_type == ActionType.MOVE_ROBBER:
        coordinate, victim = value
        value = (coordinate, None if victim is None else THEM)
    elif action.action_type == ActionType.BUILD_ROAD:
        value = tuple(sorted(value))
    return (action.action_type, value)


def action_index(action):
    return _action_index()[action_key(action)]


def legal_indices(actions):
    """Indices of these actions, in the same order."""
    index = _action_index()
    return [index[action_key(a)] for a in actions]


# ===== the board (static)
class _MapCache:
    def __init__(self, catan_map):
        tile_ids = sorted(catan_map.tiles_by_id)
        assert len(tile_ids) == NUM_TILES
        index_of_id = {tid: i for i, tid in enumerate(tile_ids)}
        self.tile_of_coordinate = {
            c: index_of_id[t.id] for c, t in catan_map.land_tiles.items()
        }
        tiles = np.zeros((NUM_TILES, 7), np.float32)
        for i, tid in enumerate(tile_ids):
            tile = catan_map.tiles_by_id[tid]
            if tile.resource is None:
                tiles[i, 5] = 1
            else:
                tiles[i, RESOURCES.index(tile.resource)] = 1
                tiles[i, 6] = number_probability(tile.number) * _SCALE_PROBA
        # Per corner: its production of each resource, and its port.
        self.node_production = np.zeros((NUM_NODES, 5), np.float32)
        for node, counter in catan_map.node_production.items():
            for r, p in counter.items():
                self.node_production[node, RESOURCES.index(r)] = p * _SCALE_PROBA
        self.node_port = np.zeros((NUM_NODES, 6), np.float32)
        for kind, nodes in catan_map.port_nodes.items():
            for node in nodes:
                self.node_port[node, PORT_KINDS.index(kind)] = 1
        # Per corner, which tiles it touches (for the robber's blocking)
        self.node_tiles = {
            node: [index_of_id[t.id] for t in tiles_]
            for node, tiles_ in catan_map.adjacent_tiles.items()
        }
        # Per tile, which corners touch it
        self.tile_touches = np.zeros((NUM_TILES, NUM_NODES), np.float32)
        for node, tiles_ in self.node_tiles.items():
            for t in tiles_:
                self.tile_touches[t, node] = 1
        self.tile_production = np.zeros((NUM_TILES, 5), np.float32)
        for i, tid in enumerate(tile_ids):
            tile = catan_map.tiles_by_id[tid]
            if tile.resource is not None:
                self.tile_production[i, RESOURCES.index(tile.resource)] = (
                    number_probability(tile.number) * _SCALE_PROBA
                )
        # Plain-list copies for the per-position code
        self.tile_touches_list = [[bool(x) for x in row] for row in self.tile_touches]
        self.node_production_list = [list(map(float, r)) for r in self.node_production]
        self.node_port_kinds = [
            [k for k in range(6) if self.node_port[n, k]] for n in range(NUM_NODES)
        ]
        self.tile_production_list = [list(map(float, r)) for r in self.tile_production]
        self.static = np.concatenate(
            [tiles.ravel(), self.node_production.ravel(), self.node_port.ravel()]
        )


_maps = weakref.WeakKeyDictionary()


def _map_cache(catan_map):
    cache = _maps.get(catan_map)
    if cache is None:
        cache = _maps[catan_map] = _MapCache(catan_map)
    return cache


def encode_static(catan_map):
    return _map_cache(catan_map).static


STATIC_SIZE = NUM_TILES * 7 + NUM_NODES * 11  # 727


# ===== the position (dynamic)
# Building this is in the search's inner loop. The vectors are short, so plain
# Python lists with one conversion at the end beat many small numpy calls.
_BASE_SCALE = (1 / 15, 1, 1, 1 / 15, 1 / 5, 1 / 4, 1 / 15, 1)


@lru_cache(None)
def _keys(index):
    p = f"P{index}_"
    base = (
        "VICTORY_POINTS",
        "HAS_ARMY",
        "HAS_ROAD",
        "ROADS_AVAILABLE",
        "SETTLEMENTS_AVAILABLE",
        "CITIES_AVAILABLE",
        "LONGEST_ROAD_LENGTH",
        "HAS_ROLLED",
    )
    return (
        [(p + n, scale) for n, scale in zip(base, _BASE_SCALE)],
        [p + r + "_IN_HAND" for r in RESOURCES],
        [p + c + "_IN_HAND" for c in DEVELOPMENT_CARDS],
        [p + "PLAYED_" + c for c in PLAYABLE_DEVS],
        p + "ACTUAL_VICTORY_POINTS",
        p + "HAS_PLAYED_DEVELOPMENT_CARD_IN_TURN",
        [(p + c + "_OWNED_AT_START", p + c + "_IN_HAND") for c in PLAYABLE_DEVS],
    )


def _player_block(v, state, color, cache, robber_tile, me):
    """Appends one player's features to the list v. `me` adds what only the
    owner knows."""
    base, hand_keys, dev_keys, played_keys, actual, played_turn, playable = _keys(
        state.color_to_index[color]
    )
    ps = state.player_state
    hand = [ps[k] for k in hand_keys]
    devs = [ps[k] for k in dev_keys]
    v += [ps[k] * scale for k, scale in base]
    v.append(sum(hand) / 10)
    v.append(sum(devs) / 5)
    v += [h / 5 for h in hand]
    v += [ps[k] / 5 for k in played_keys]
    # Production per resource, with and without the robber, and ports owned.
    total = [0.0] * 5
    blocked = 0
    ports = [0.0] * 6
    buildings = state.buildings_by_color[color]
    touches = cache.tile_touches_list[robber_tile]
    for mult, kind in ((1, SETTLEMENT), (2, CITY)):
        for node in buildings[kind]:
            for r, x in enumerate(cache.node_production_list[node]):
                total[r] += mult * x
            for k in cache.node_port_kinds[node]:
                ports[k] = 1.0
            if touches[node]:
                blocked += mult
    v += [x / 3 for x in total]
    tile = cache.tile_production_list[robber_tile]
    v += [(x - blocked * t) / 3 for x, t in zip(total, tile)]
    v += ports
    if me:
        v.append(ps[actual] / 15)
        v.append(ps[played_turn])
        v += [d / 3 for d in devs]
        # playable now: owned since the start of the turn
        v += [float(bool(ps[a] and ps[b] > 0)) for a, b in playable]


PLAYER_SIZE = 10 + 5 + 4 + 5 + 5 + 6  # 35
MY_EXTRA = 2 + 5 + 4  # 11
_PLAIN_ODDS = [min(t - 1, 13 - t) / 36 * 6 for t in range(2, 13)]


def _dice_block(v, dice, me, them):
    """Appends the balanced dice, as the log shows them: cards left of each
    total, recent rolls in order, 7 tallies, the streak, and the odds of the
    next roll for each player."""
    if dice is None:
        v += [0.0] * (DICE_SIZE - 22)
        v += _PLAIN_ODDS
        v += _PLAIN_ODDS
        return
    # The block only changes when the dice do (a roll), but search encodes
    # many positions per roll: keep it in the dice's own cache, which every
    # change to the dice clears and copies of the game share.
    key = ("encoded", me, them)
    cached = dice._cache.get(key)
    if cached is None:
        cached = dice._cache[key] = _dice_features(dice, me, them)
    v += cached


def _dice_features(dice, me, them):
    left = dice.cards_left
    v = [
        float(dice.mode == "balanced"),
        left / 36,
        float(left < MIN_CARDS_BEFORE_RESHUFFLE),
    ]
    v += [len(p) / 6 for p in dice.deck]
    recent = [0.0] * 55
    at = -2  # total 2 is the first of each slot's 11
    for total in reversed(dice.recent):  # most recent first
        recent[at + total] = 1.0
        at += 11
    v += recent
    sevens = dice.sevens
    streak_color = dice.streak_color
    v += [
        sevens.get(me, 0) / 5,
        sevens.get(them, 0) / 5,
        float(streak_color == me),
        float(streak_color == them),
        dice.streak_count / 3,
    ]
    v += [x * 6 for x in dice.odds(me)]
    v += [x * 6 for x in dice.odds(them)]
    return v


DICE_SIZE = 96
_BOARD_SIZE = NUM_TILES + NUM_NODES * 4 + NUM_EDGES * 2
_NODES_AT = NUM_TILES
_ROADS_AT = _NODES_AT + NUM_NODES * 4


def encode(game, color):
    """(static, dynamic) vectors for the position as `color` sees it."""
    state = game.state
    board = state.board
    cache = _map_cache(board.map)
    c0, c1 = state.colors
    them = c1 if color == c0 else c0
    robber_tile = cache.tile_of_coordinate[board.robber_coordinate]

    v = [0.0] * _BOARD_SIZE
    v[robber_tile] = 1.0
    for side, c in enumerate((color, them)):
        b = state.buildings_by_color[c]
        for node in b[SETTLEMENT]:
            v[_NODES_AT + 4 * node + 2 * side] = 1.0
        for node in b[CITY]:
            v[_NODES_AT + 4 * node + 2 * side + 1] = 1.0
        for edge in b[ROAD]:
            v[_ROADS_AT + 2 * EDGE_INDEX[edge] + side] = 1.0

    _player_block(v, state, color, cache, robber_tile, me=True)
    _player_block(v, state, them, cache, robber_tile, me=False)
    me_index = state.color_to_index[color]
    v += [
        float(state.is_initial_build_phase),
        float(state.is_discarding),
        state.discard_counts[me_index] / 5,
        float(state.is_moving_knight),
        float(state.is_road_building),
        state.free_roads_available / 2,
        float(state.current_turn_index == me_index),
        min(state.num_turns, 300) / 100,
        len(state.development_listdeck) / 25,
    ]
    v += [n / 19 for n in state.resource_freqdeck]
    _dice_block(v, state.dice, color, them)
    return cache.static, np.array(v, np.float32)


PHASE_SIZE = 9 + 5
DYNAMIC_SIZE = (
    NUM_TILES
    + NUM_NODES * 4
    + NUM_EDGES * 2
    + PLAYER_SIZE * 2
    + MY_EXTRA
    + PHASE_SIZE
    + DICE_SIZE
)  # 570
INPUT_SIZE = STATIC_SIZE + DYNAMIC_SIZE


def encode_flat(game, color):
    static, dynamic = encode(game, color)
    return np.concatenate([static, dynamic])


__all__ = [
    "INPUT_SIZE",
    "NUM_ACTIONS",
    "action_index",
    "action_table",
    "encode",
    "encode_flat",
    "legal_indices",
]
