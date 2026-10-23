"""Raw-net calibration on ranked games (data/ranked/bot_value), by value bucket and phase.
Usage: raw_cal.py NET
Stated win chance (v+1)/2 vs realised win rate; intervals are game-clustered (+-1.96 se).
"""

import sys

import numpy as np

from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net

net = Net.load(sys.argv[1])
parts = load_value_shards(["/home/owen/colonist-ai/data/ranked/bot_value"])
V, Z, T, G = [], [], [], []
for i, (static, dynamic, game, z, turn) in enumerate(parts):
    _, v = net.forward_rows([static[g] for g in game], dynamic)
    V.append(np.asarray(v, np.float64)), Z.append(z), T.append(turn), G.append(
        np.full(len(z), i)
    )
V, Z, T, G = (np.concatenate(x) for x in (V, Z, T, G))
print(
    f"{len(parts)} games, {len(V)} positions, win rate {np.mean([p[3][0] > 0 for p in parts]):.1%}"
)
edges = [-1.01, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 1.01]
for name, lo, hi in (
    ("middle 20-60", 20, 60),
    ("late 60+", 60, 10**6),
    ("all 20+", 20, 10**6),
):
    print(name)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (T >= lo) & (T < hi) & (V >= a) & (V < b)
        if m.sum() < 50:
            continue
        games = np.unique(G[m])
        # per-game mean of (win - stated), to cluster
        d = np.array(
            [np.mean((Z[m & (G == g)] > 0) - (V[m & (G == g)] + 1) / 2) for g in games]
        )
        se = d.std(ddof=1) / np.sqrt(len(d))
        print(
            f"  v [{a:+.1f},{b:+.1f}): n {m.sum():6d} games {len(games):4d} "
            f"stated {np.mean((V[m] + 1) / 2):.1%} won {np.mean(Z[m] > 0):.1%} "
            f"gap {d.mean():+.1%} +-{1.96 * se:.1%}"
        )
