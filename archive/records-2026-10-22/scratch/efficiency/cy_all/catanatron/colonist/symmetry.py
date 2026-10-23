"""The 12 symmetries of the board, for training (PLAN.md, Phase 2).

The 19-hex board looks the same after any of 6 rotations, each with or
without a mirror flip. Applying one to a position relabels its tiles, corners
and roads (ports and numbers travel with their corners and tiles) and gives
another position with the same outcome and the correspondingly relabelled
best move. Training on randomly transformed positions shows the network up to
12 times as many distinct boards from the same games, which matters most for
the value head (each game contributes only one result).

Each symmetry is a pair of index arrays, applied as

    x_new  = x[:, sym.inputs]              (the network's inputs)
    pi_new = pi[:, sym.moves]              (anything indexed by move: policy, legal mask)
    a_new  = sym.move_image[a]             (a single move index)

Tile and corner numbering is the same on every base board (only resources,
numbers and ports are shuffled), so the arrays are computed once.
"""

from functools import lru_cache

import numpy as np

from catanatron.colonist import encode as E
from catanatron.models.enums import ActionType, NodeRef
from catanatron.models.map import build_map

CORNERS = [
    NodeRef.NORTH,
    NodeRef.NORTHEAST,
    NodeRef.SOUTHEAST,
    NodeRef.SOUTH,
    NodeRef.SOUTHWEST,
    NodeRef.NORTHWEST,
]  # clockwise


def _rotate(c):  # 60 degrees, in cube coordinates
    x, y, z = c
    return (-z, -x, -y)


def _mirror(c):
    x, y, z = c
    return (x, z, y)


class Symmetry:
    def __init__(self, tile_image, node_image):
        """tile_image[i], node_image[n]: where tile index i and corner n go."""
        self.tile_image = np.asarray(tile_image)
        self.node_image = np.asarray(node_image)
        self.edge_image = np.asarray(
            [
                E.EDGE_INDEX[tuple(sorted((node_image[a], node_image[b])))]
                for a, b in E.EDGES
            ]
        )
        self.inputs = self._input_gather()
        self.move_image = self._move_image()
        self.moves = np.argsort(self.move_image)  # gather form

    def _input_gather(self):
        """perm with x_new = x[perm]: new slot image(i) reads old slot i."""
        perm = np.arange(E.INPUT_SIZE)

        def block(start, width, image):
            for i, j in enumerate(image):
                perm[start + j * width : start + (j + 1) * width] = np.arange(
                    start + i * width, start + (i + 1) * width
                )

        block(0, 7, self.tile_image)  # static: tiles
        block(E.NUM_TILES * 7, 5, self.node_image)  # static: corner production
        block(E.NUM_TILES * 7 + E.NUM_NODES * 5, 6, self.node_image)  # corner ports
        d = E.STATIC_SIZE
        block(d, 1, self.tile_image)  # robber
        block(d + E.NUM_TILES, 4, self.node_image)  # buildings
        block(d + E.NUM_TILES + E.NUM_NODES * 4, 2, self.edge_image)  # roads
        return perm

    def _move_image(self):
        index = {key: i for i, key in enumerate(E.action_table())}
        coordinate_of = {i: c for c, i in _tile_index().items()}
        tile_of = _tile_index()
        image = np.arange(E.NUM_ACTIONS)
        for i, (t, value) in enumerate(E.action_table()):
            if t in (ActionType.BUILD_SETTLEMENT, ActionType.BUILD_CITY):
                value = int(self.node_image[value])
            elif t == ActionType.BUILD_ROAD:
                value = E.EDGES[self.edge_image[E.EDGE_INDEX[value]]]
            elif t == ActionType.MOVE_ROBBER:
                coordinate, victim = value
                value = (coordinate_of[self.tile_image[tile_of[coordinate]]], victim)
            image[i] = index[(t, value)]
        return image


@lru_cache(None)
def _tile_index():
    """Coordinate -> tile index, as encode numbers tiles (the same on every
    base board)."""
    catan_map = build_map("BASE")
    return dict(E._map_cache(catan_map).tile_of_coordinate)


@lru_cache(None)
def symmetries():
    """All 12, the identity first."""
    catan_map = build_map("BASE")
    tiles = catan_map.land_tiles
    tile_index = _tile_index()
    transforms = []
    for flip in (False, True):
        for turns in range(6):

            def f(c, flip=flip, turns=turns):
                if flip:
                    c = _mirror(c)
                for _ in range(turns):
                    c = _rotate(c)
                return c

            transforms.append(f)
    result = []
    for f in transforms:
        tile_image = [0] * E.NUM_TILES
        for c in tiles:
            tile_image[tile_index[c]] = tile_index[f(c)]
        node_image = _corner_map(tiles, f)
        result.append(Symmetry(tile_image, node_image))
    return result


def _corner_map(tiles, f):
    """Which corner each corner goes to under the tile map f: the one map of
    a hexagon's 6 corners onto itself (rotation or reflection) that agrees
    wherever tiles share a corner."""
    for reflect in (False, True):
        for shift in range(6):
            node_image = {}
            ok = True
            for c, tile in tiles.items():
                target = tiles[f(c)]
                for k, ref in enumerate(CORNERS):
                    k2 = ((-k if reflect else k) + shift) % 6
                    n, n2 = tile.nodes[ref], target.nodes[CORNERS[k2]]
                    if node_image.setdefault(n, n2) != n2:
                        ok = False
                        break
                if not ok:
                    break
            if ok and len(set(node_image.values())) == E.NUM_NODES:
                return [node_image[n] for n in range(E.NUM_NODES)]
    raise RuntimeError("no corner map fits this tile map")
