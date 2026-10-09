"""A board-aware network: message passing over the board's corners, roads and
tiles (a graph neural network), for the same inputs and moves as net.py.

The fully connected net sees a position as one long vector, so whatever it
learns about one corner it must learn again for every other. This one keeps
the board's shape. Each corner, road edge and tile has its own feature
vector, and the same small learned rule updates every one of them from its
neighbours:

  - a corner from its neighbouring corners, its road edges and its tiles,
  - a road edge from its two corners,
  - a tile from its six corners,

and each of them also from a global vector (hands, phase, dice, bank), which in
turn reads the average of each kind. After a few rounds of this, the policy
scores each move from where it happens: a settlement or city from its corner's
vector, a road from its edge's, a robber move from its tile's; the other moves
(rolls, trades, cards, ending the turn) and the value come from the global
vector.

The inputs are encode.py's (static, dynamic) vectors, sliced per corner, edge
and tile, so the same training data serves both nets. Tile, corner and edge
numbering is the same on every base board (only resources, numbers and ports
move), so the graph is built once.

Layout: per-kind activations are (count, batch, features), so a layer is one
matrix product over all corners (or edges, tiles) of all positions, and
gathering from neighbours is one product with a fixed averaging matrix.
"""

from functools import lru_cache

import numpy as np

from catanatron.colonist import encode as E

TILE_FEATURES = 7 + 1  # resource, number odds; the robber
NODE_FEATURES = 5 + 6 + 4  # production, port; buildings
EDGE_FEATURES = 2  # roads
_STATIC_NODES = E.NUM_TILES * 7
_STATIC_PORTS = _STATIC_NODES + E.NUM_NODES * 5
_DYN = E.STATIC_SIZE
_DYN_NODES = _DYN + E.NUM_TILES
_DYN_ROADS = _DYN_NODES + E.NUM_NODES * 4
_DYN_GLOBAL = _DYN_ROADS + E.NUM_EDGES * 2
GLOBAL_FEATURES = E.INPUT_SIZE - _DYN_GLOBAL  # 191


@lru_cache(None)
def topology():
    """Index and averaging arrays for the base board, built once."""
    from catanatron.models.map import build_map

    catan_map = build_map("BASE")
    T, N, M = E.NUM_TILES, E.NUM_NODES, E.NUM_EDGES
    tile_in = np.array(
        [[t * 7 + k for k in range(7)] + [_DYN + t] for t in range(T)], np.int64
    )
    node_in = np.array(
        [
            [_STATIC_NODES + n * 5 + k for k in range(5)]
            + [_STATIC_PORTS + n * 6 + k for k in range(6)]
            + [_DYN_NODES + n * 4 + k for k in range(4)]
            for n in range(N)
        ],
        np.int64,
    )
    edge_in = np.array([[_DYN_ROADS + e * 2 + k for k in range(2)] for e in range(M)])

    def mean(pairs, rows, cols):
        a = np.zeros((rows, cols), np.float32)
        for r, c in pairs:
            a[r, c] = 1
        return a / np.maximum(a.sum(1, keepdims=True), 1)

    node_node = [(a, b) for a, b in E.EDGES] + [(b, a) for a, b in E.EDGES]
    node_edge = [(n, e) for e, pair in enumerate(E.EDGES) for n in pair]
    node_tile = [
        (n, t.id) for n, tiles in catan_map.adjacent_tiles.items() for t in tiles
    ]
    # Moves by where they happen, and the rest
    table = E.action_table()
    tile_of = {c: t.id for c, t in catan_map.land_tiles.items()}
    settle, city = np.zeros(N, np.int64), np.zeros(N, np.int64)
    road = np.zeros(M, np.int64)
    robber = np.zeros((T, 2), np.int64)
    other = []
    from catanatron.models.enums import ActionType

    for i, (kind, value) in enumerate(table):
        if kind == ActionType.BUILD_SETTLEMENT:
            settle[value] = i
        elif kind == ActionType.BUILD_CITY:
            city[value] = i
        elif kind == ActionType.BUILD_ROAD:
            road[E.EDGE_INDEX[value]] = i
        elif kind == ActionType.MOVE_ROBBER:
            coordinate, victim = value
            robber[tile_of[coordinate], 0 if victim is None else 1] = i
        else:
            other.append(i)
    return {
        "tile_in": tile_in,
        "node_in": node_in,
        "edge_in": edge_in,
        "node_node": mean(node_node, N, N),
        "node_edge": mean(node_edge, N, M),
        "node_tile": mean(node_tile, N, T),
        "edge_node": mean([(e, n) for n, e in node_edge], M, N),
        "tile_node": mean([(t, n) for n, t in node_tile], T, N),
        "settle": settle,
        "city": city,
        "road": road,
        "robber": robber,
        "other": np.array(other, np.int64),
    }


NUM_OTHER = 95


def shapes(hidden=32, glob=64, layers=3):
    """Every weight's (in, out), by name. The forward passes below and the
    PyTorch model share these names."""
    H, G = hidden, glob
    s = {
        "tile_in": (TILE_FEATURES, H),
        "node_in": (NODE_FEATURES, H),
        "edge_in": (EDGE_FEATURES, H),
        "glob_in": (GLOBAL_FEATURES, G),
    }
    for l in range(layers):
        s[f"node{l}"] = (4 * H + G, H)
        s[f"edge{l}"] = (2 * H + G, H)
        s[f"tile{l}"] = (2 * H + G, H)
        s[f"glob{l}"] = (G + 3 * H, G)
    s.update(
        {
            "node_out": (H, 2),
            "edge_out": (H, 1),
            "tile_out": (H, 2),
            "glob_out": (G, NUM_OTHER),
            "value1": (G, G),
            "value2": (G, 1),
        }
    )
    return s


def forward(params, x, layers, xp=np):
    """Logits (batch, 313) and values (batch,) for inputs x (batch, 1297).
    params: name -> (W (in, out), b (out,)). `xp` is numpy or torch (whose
    functions this uses are named alike), so play and training share one
    definition."""
    topo = _topology(xp, x)
    b = x.shape[0]

    def linear(name, h):  # h: (count, batch, in)
        w, bias = params[name]
        return (h.reshape(-1, h.shape[-1]) @ w + bias).reshape(*h.shape[:-1], -1)

    def relu(h):
        return np.maximum(h, 0) if xp is np else xp.relu(h)

    def gather(matrix, h):  # (rows, count) @ (count, batch, H)
        n, bb, H = h.shape
        return (matrix @ h.reshape(n, bb * H)).reshape(matrix.shape[0], bb, H)

    def per(index):  # (count, features) indexes -> (count, batch, features)
        return (
            xp.permute(x[:, index], (1, 0, 2))
            if xp is not np
            else (x[:, index].transpose(1, 0, 2))
        )

    t = relu(linear("tile_in", per(topo["tile_in"])))
    n = relu(linear("node_in", per(topo["node_in"])))
    e = relu(linear("edge_in", per(topo["edge_in"])))
    g = relu(x[:, _DYN_GLOBAL:] @ params["glob_in"][0] + params["glob_in"][1])
    cat = xp.concatenate if xp is np else xp.cat
    for l in range(layers):

        def wide(count):
            return xp.broadcast_to(g[None], (count, *g.shape))

        n2 = relu(
            linear(
                f"node{l}",
                cat(
                    [
                        n,
                        gather(topo["node_node"], n),
                        gather(topo["node_edge"], e),
                        gather(topo["node_tile"], t),
                        wide(E.NUM_NODES),
                    ],
                    -1,
                ),
            )
        )
        e2 = relu(
            linear(
                f"edge{l}",
                cat([e, gather(topo["edge_node"], n), wide(E.NUM_EDGES)], -1),
            )
        )
        t2 = relu(
            linear(
                f"tile{l}",
                cat([t, gather(topo["tile_node"], n), wide(E.NUM_TILES)], -1),
            )
        )
        pooled = cat([g, n.mean(0), e.mean(0), t.mean(0)], -1)
        g2 = relu(pooled @ params[f"glob{l}"][0] + params[f"glob{l}"][1])
        n, e, t, g = n + n2, e + e2, t + t2, g + g2

    node = linear("node_out", n)  # (54, batch, 2)
    edge = linear("edge_out", e)[..., 0]  # (72, batch)
    tile = linear("tile_out", t)  # (19, batch, 2)
    other = g @ params["glob_out"][0] + params["glob_out"][1]
    if xp is np:
        logits = np.empty((b, E.NUM_ACTIONS), x.dtype)
    else:
        logits = xp.empty((b, E.NUM_ACTIONS), dtype=x.dtype, device=x.device)
    logits[:, topo["settle"]] = node[..., 0].T
    logits[:, topo["city"]] = node[..., 1].T
    logits[:, topo["road"]] = edge.T
    logits[:, topo["robber"][:, 0]] = tile[..., 0].T
    logits[:, topo["robber"][:, 1]] = tile[..., 1].T
    logits[:, topo["other"]] = other
    v = relu(g @ params["value1"][0] + params["value1"][1])
    values = xp.tanh(v @ params["value2"][0] + params["value2"][1])[:, 0]
    return logits, values


def _topology(xp, x):
    topo = topology()
    if xp is np:
        return topo
    key = (str(x.device), x.dtype)
    cache = _torch_topology.setdefault(key, {})
    if not cache:
        for k, v in topo.items():
            t = xp.from_numpy(v).to(x.device)
            cache[k] = t.to(x.dtype) if v.dtype == np.float32 else t
    return cache


_torch_topology = {}


class GraphNet:
    """The graph network for play, in numpy: the same interface as net.Net."""

    def __init__(self, params, hidden, glob, layers, meta=None):
        self.params = {
            k: (np.asarray(w, np.float32), np.asarray(b, np.float32))
            for k, (w, b) in params.items()
        }
        self.hidden_size, self.glob, self.layers = hidden, glob, layers
        self.meta = meta or {}

    @staticmethod
    def load(z):
        """From an opened npz with arch == "graph"."""
        hidden, glob, layers = (int(z[k]) for k in ("g_hidden", "g_glob", "g_layers"))
        params = {
            name: (z[f"w_{name}"], z[f"b_{name}"])
            for name in shapes(hidden, glob, layers)
        }
        meta = {k: z[k] for k in z.files if k.startswith("meta_")}
        return GraphNet(params, hidden, glob, layers, meta)

    def save(self, path):
        arrays = {
            "arch": np.asarray("graph"),
            "g_hidden": np.asarray(self.hidden_size),
            "g_glob": np.asarray(self.glob),
            "g_layers": np.asarray(self.layers),
        }
        for name, (w, b) in self.params.items():
            arrays[f"w_{name}"] = w
            arrays[f"b_{name}"] = b
        np.savez(path, **arrays, **self.meta)

    def forward(self, static, dynamic):
        x = np.concatenate(
            [np.broadcast_to(static, (len(dynamic), len(static))), dynamic], 1
        ).astype(np.float32)
        return forward(self.params, x, self.layers)

    def forward_rows(self, statics, dynamic):
        x = np.concatenate([np.stack(statics), dynamic], 1).astype(np.float32)
        return forward(self.params, x, self.layers)

    def evaluate(self, game, color, legal=None):
        static, dynamic = E.encode(game, color)
        logits, values = self.forward(static, dynamic[None, :].astype(np.float32))
        if legal is None:
            return logits[0], float(values[0])
        x = logits[0, legal]
        x = np.exp(x - x.max())
        return x / x.sum(), float(values[0])


def random_graph_net(hidden=32, glob=64, layers=3, seed=0):
    """An untrained graph net (for tests and benchmarks)."""
    rng = np.random.default_rng(seed)
    params = {}
    for name, (a, b) in shapes(hidden, glob, layers).items():
        scale = 0.01 if name.endswith("_out") or name == "value2" else (2 / a) ** 0.5
        params[name] = (
            rng.normal(0, scale, (a, b)).astype(np.float32),
            np.zeros(b, np.float32),
        )
    return GraphNet(params, hidden, glob, layers)


def torch_model(hidden=32, glob=64, layers=3):
    """The same network in PyTorch, for training (weights shaped (in, out),
    as forward() multiplies them)."""
    import torch
    import torch.nn as nn

    class GraphModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.config = (hidden, glob, layers)
            self.w = nn.ParameterDict()
            self.b = nn.ParameterDict()
            for name, (a, b) in shapes(hidden, glob, layers).items():
                std = (
                    0.01
                    if name.endswith("_out") or name == "value2"
                    else (2 / a) ** 0.5
                )
                self.w[name] = nn.Parameter(torch.randn(a, b) * std)
                self.b[name] = nn.Parameter(torch.zeros(b))

        def params(self):
            return {k: (self.w[k], self.b[k]) for k in self.w}

        def forward(self, x):
            return forward(self.params(), x, layers, xp=torch)

    return GraphModel()
