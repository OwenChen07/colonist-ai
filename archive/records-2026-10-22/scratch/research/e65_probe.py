"""E65 step 0: do road-plan features explain results that round 73's value misses?
Usage: e65_probe.py NET FOLDER [FOLDER ...]   (value shards: static, dynamic, game, z, turn)

Per position (turn >= 20, mover's view): L0-L3, join, spot for me and for them
(e63_plan.plan_features on the board decoded from dynamic). Then
 (b) z - v regressed on [1] (bias only) vs [1, features, features * v], 5-fold, folds by game;
     MSE of v, of v + bias fit, of v + features fit, by phase; paired game-clustered se of the gain;
 (a) ridge from the net's global vector g to each feature, 5-fold by game: R2."""

import sys
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist import graph
from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net
from e63_plan import plan_features

NODES_AT, ROADS_AT = E._NODES_AT, E._ROADS_AT
LAND = set(range(E.NUM_NODES))


class Board:
    def __init__(self, dyn):
        self.buildings, self.roads = {}, {}
        for n in range(E.NUM_NODES):
            for side in (0, 1):
                if dyn[NODES_AT + 4 * n + 2 * side] > 0.5:
                    self.buildings[n] = (side, "S")
                elif dyn[NODES_AT + 4 * n + 2 * side + 1] > 0.5:
                    self.buildings[n] = (side, "C")
        for i, (a, b) in enumerate(E.EDGES):
            for side in (0, 1):
                if dyn[ROADS_AT + 2 * i + side] > 0.5:
                    self.roads[(a, b)] = self.roads[(b, a)] = side
        self.map = type("M", (), {"land_nodes": LAND})


def glob_vector(net, statics, dynamic):
    """Round 73's global vector g, via the aux head set to the identity."""
    p = dict(net.params)
    G = p["value1"][0].shape[0]
    p["aux1"] = (np.eye(G, dtype=np.float32), np.zeros(G, np.float32))
    p["aux2"] = (np.eye(G, dtype=np.float32), np.zeros(G, np.float32))
    x = np.concatenate([np.stack(statics), dynamic], 1).astype(np.float32)
    _, v, g = graph.forward(p, x, net.layers, aux=True)
    return v, g


def main(net_path, *folders):
    net = Net.load(net_path)
    parts = load_value_shards(list(folders))
    V, Z, T, GID, F, GV = [], [], [], [], [], []
    t0 = time.time()
    for i, (static, dynamic, game, z, turn) in enumerate(parts):
        keep = turn >= 20
        if not keep.any():
            continue
        st = [static[k] for k in game[keep]]
        dy = dynamic[keep]
        v, g = glob_vector(net, st, dy)
        for row in dy:
            b = Board(row)
            f = []
            for side in (0, 1):
                best, join, spot = plan_features(b, side)
                f += [*best, join, spot]
            F.append(f)
        V.append(np.asarray(v, np.float64)), Z.append(z[keep]), T.append(turn[keep])
        GID.append(np.full(keep.sum(), i)), GV.append(np.asarray(g, np.float64))
        if i % 100 == 0:
            print(f"  {i}/{len(parts)} games, {time.time() - t0:.0f} s", flush=True)
    V, Z, T, GID, GV = (np.concatenate(x) for x in (V, Z, T, GID, GV))
    F = np.asarray(F, np.float64)
    print(
        f"{len(parts)} games, {len(V)} positions (turn 20+), features {F.mean(0).round(2)}"
    )

    rng = np.random.default_rng(0)
    fold = rng.integers(0, 5, GID.max() + 1)[GID]
    Fs = (F - F.mean(0)) / (F.std(0) + 1e-9)
    X_bias = np.ones((len(V), 1))
    X_feat = np.column_stack([X_bias, Fs, Fs * V[:, None]])

    def cv_pred(X, y, lam=1.0):
        out = np.zeros(len(y))
        for k in range(5):
            tr, te = fold != k, fold == k
            A = X[tr].T @ X[tr] + lam * np.eye(X.shape[1])
            w = np.linalg.solve(A, X[tr].T @ y[tr])
            out[te] = X[te] @ w
        return out

    r = Z - V
    pb = np.clip(V + cv_pred(X_bias, r), -1, 1)
    pf = np.clip(V + cv_pred(X_feat, r), -1, 1)
    for name, lo, hi in (
        ("all 20+", 20, 10**6),
        ("mid 20-60", 20, 60),
        ("late 60+", 60, 10**6),
    ):
        m = (T >= lo) & (T < hi)
        e0, eb, ef = ((p[m] - Z[m]) ** 2 for p in (V, pb, pf))
        games = np.unique(GID[m])
        d = np.array([np.mean(eb[GID[m] == g] - ef[GID[m] == g]) for g in games])
        print(
            f"(b) {name}: n {m.sum()} games {len(games)} mse v {e0.mean():.4f} "
            f"v+bias {eb.mean():.4f} v+features {ef.mean():.4f} "
            f"gain {eb.mean() - ef.mean():+.4f} (per-game mean {d.mean():+.4f} +-{1.96 * d.std(ddof=1) / np.sqrt(len(d)):.4f})"
        )
    Xg = np.column_stack([np.ones(len(V)), GV])
    names = [
        f"{s}{n}"
        for s in ("me_", "them_")
        for n in ("L0", "L1", "L2", "L3", "join", "spot")
    ]
    r2 = []
    for j, name in enumerate(names):
        y = F[:, j]
        p = cv_pred(Xg, y, lam=10.0)
        r2.append(1 - np.mean((y - p) ** 2) / max(y.var(), 1e-9))
    print("(a) R2 from g: " + "  ".join(f"{n} {x:.2f}" for n, x in zip(names, r2)))
    print(
        f"(a) L3-L0 gain R2: me {1 - np.var((F[:,3]-F[:,0]) - cv_pred(Xg, F[:,3]-F[:,0], 10.0)) / max(np.var(F[:,3]-F[:,0]),1e-9):.2f}"
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
