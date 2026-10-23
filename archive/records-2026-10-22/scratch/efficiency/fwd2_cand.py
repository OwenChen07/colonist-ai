"""Candidate forward_np: corners, edges and tiles in one buffer with a zero row
at the end, so a layer's five neighbour means are one set of takes (a missing
neighbour takes the zero row), and in-place bias, relu and residual adds."""

import os
from functools import lru_cache

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.graph import _DYN_GLOBAL, topology

from catanatron.colonist.graph import forward_np as _old

THRESH = int(os.environ.get("THRESH", "100000"))
GMERGE = os.environ.get("GMERGE") == "1"
_KINDS = ("node_node", "node_edge", "node_tile", "edge_node", "tile_node")


@lru_cache(None)
def combined_gathers():
    """(slots, tail, weight): the gathered rows are the five means' rows in
    _KINDS order; slots[k] indexes each row's k-th neighbour in the buffer
    [nodes; edges; tiles; zero row], for k < 3 over all rows and for k >= 3
    over the last `tail` rows only (only a tile has more than 3)."""
    topo = topology()
    N, M, T = E.NUM_NODES, E.NUM_EDGES, E.NUM_TILES
    offset = {"node": 0, "edge": N, "tile": N + M}
    zero = N + M + T
    lists, weights = [], []
    for name in _KINDS:
        a = topo[name]
        src = offset[name.split("_")[1]]
        for row in a:
            cols = np.nonzero(row)[0]
            lists.append(cols + src)
            weights.append(row[cols[0]])
    width = max(map(len, lists))
    idx = np.full((len(lists), width), zero, np.intp)
    for r, c in enumerate(lists):
        idx[r, : len(c)] = c
    tail = sum(len(c) > 3 for c in lists)
    assert all(len(c) > 3 for c in lists[-tail:]) and tail == T
    slots = [idx[:, k].copy() for k in range(3)]
    slots += [idx[-tail:, k].copy() for k in range(3, width)]
    return slots, tail, np.array(weights, np.float32)[:, None, None]


def forward_np(params, statics, dynamic, layers):
    if len(dynamic) > THRESH:
        return _old(params, statics, dynamic, layers)
    x = np.concatenate([np.stack(statics), dynamic], 1).astype(np.float32)
    topo = topology()
    slots, tail, weight = combined_gathers()
    b = x.shape[0]
    H = params["tile_in"][0].shape[1]
    N, M, T = E.NUM_NODES, E.NUM_EDGES, E.NUM_TILES
    s = np.empty((N + M + T + 1, b, H), np.float32)
    s[-1] = 0
    n, e, t = s[:N], s[N : N + M], s[N + M : N + M + T]
    a, c = 3 * N, 3 * N + M  # gathered rows: node means, edge's, tile's

    def linear(name, h):
        w, bias = params[name]
        y = h.reshape(-1, h.shape[-1]) @ w
        y += bias
        return y.reshape(*h.shape[:-1], -1)

    def per(index):
        return x[:, index].transpose(1, 0, 2)

    def mean0(h):
        r = np.add.reduce(h, 0)
        return np.true_divide(r, np.intp(h.shape[0]), out=r, casting="unsafe")

    p = np.empty((N + M + T, b, H), np.float32)  # each layer's update
    pn, pe, pt = p[:N], p[N : N + M], p[N + M :]
    starts = np.array([0, N, N + M], np.intp)
    counts = np.array([N, M, T], np.float32)[:, None, None]
    gw = (
        [
            (
                np.concatenate(
                    [
                        params[f"{k}{l}"][0][-params["glob_in"][0].shape[1] :]
                        for k in ("node", "edge", "tile")
                    ],
                    1,
                ),
                np.concatenate(
                    [params[f"{k}{l}"][1] for k in ("node", "edge", "tile")]
                ),
            )
            for l in range(layers)
        ]
        if GMERGE
        else None
    )

    np.maximum(linear("tile_in", per(topo["tile_in"])), 0, out=t)
    np.maximum(linear("node_in", per(topo["node_in"])), 0, out=n)
    np.maximum(linear("edge_in", per(topo["edge_in"])), 0, out=e)
    g = np.maximum(x[:, _DYN_GLOBAL:] @ params["glob_in"][0] + params["glob_in"][1], 0)
    cat = np.concatenate
    take = s.take
    for l in range(layers):
        m = take(slots[0], 0)
        np.add(m, take(slots[1], 0), out=m)
        np.add(m, take(slots[2], 0), out=m)
        mt = m[len(m) - tail :]
        for sl in slots[3:]:
            np.add(mt, take(sl, 0), out=mt)
        np.multiply(m, weight, out=m)
        hn = cat([n, m[:N], m[N : 2 * N], m[2 * N : a]], -1)
        he = cat([e, m[a:c]], -1)
        ht = cat([t, m[c:]], -1)
        sums = np.add.reduceat(s[:-1], starts, 0)
        np.true_divide(sums, counts, out=sums)
        pooled = cat([g, sums[0], sums[1], sums[2]], -1)
        g2 = np.maximum(pooled @ params[f"glob{l}"][0] + params[f"glob{l}"][1], 0)
        if GMERGE:
            gp = g @ gw[l][0]
            gp += gw[l][1]
            gps = gp[:, :H], gp[:, H : 2 * H], gp[:, 2 * H :]
        else:
            gps = []
            for kind, h in (("node", hn), ("edge", he), ("tile", ht)):
                w, bias = params[f"{kind}{l}"]
                gps.append(g @ w[h.shape[-1] :] + bias)
        for kind, h, out, gpk in (
            ("node", hn, pn, gps[0]),
            ("edge", he, pe, gps[1]),
            ("tile", ht, pt, gps[2]),
        ):
            k = h.shape[-1]
            np.matmul(
                h.reshape(-1, k), params[f"{kind}{l}"][0][:k], out=out.reshape(-1, H)
            )
            out += gpk
        np.maximum(p, 0, out=p)
        np.add(s[:-1], p, out=s[:-1])
        g = g + g2

    node = linear("node_out", n)
    edge = linear("edge_out", e)[..., 0]
    tile = linear("tile_out", t)
    other = g @ params["glob_out"][0] + params["glob_out"][1]
    logits = np.empty((b, E.NUM_ACTIONS), x.dtype)
    logits[:, topo["settle"]] = node[..., 0].T
    logits[:, topo["city"]] = node[..., 1].T
    logits[:, topo["road"]] = edge.T
    logits[:, topo["robber"][:, 0]] = tile[..., 0].T
    logits[:, topo["robber"][:, 1]] = tile[..., 1].T
    logits[:, topo["other"]] = other
    v = np.maximum(g @ params["value1"][0] + params["value1"][1], 0)
    values = np.tanh(v @ params["value2"][0] + params["value2"][1])[:, 0]
    return logits, values
