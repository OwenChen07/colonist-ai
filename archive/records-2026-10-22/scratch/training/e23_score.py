"""E23 scoring: name=net.npz ...; the first net is the baseline. Holdout = data/human/holdout/value.

Per net: MSE, turn 75+ MSE and bias, v>=0.5 claim vs result. Paired against the baseline by
game (bootstrap over games, 2000 draws): overall MSE change and turn 75+ bias change.
"""

import sys

import numpy as np

from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net

HOLDOUT = "data/human/holdout/value"
parts = load_value_shards([HOLDOUT])
Z = np.concatenate([p[3] for p in parts]).astype(np.float64)
T = np.concatenate([p[4] for p in parts])
G = np.concatenate([np.full(len(p[3]), i) for i, p in enumerate(parts)])
n_games = len(parts)
late = T >= 75
print(f"{n_games} games, {len(Z)} positions, {late.sum()} at turn 75+")

values = {}
for arg in sys.argv[1:]:
    name, path = arg.split("=", 1)
    net = Net.load(path)
    vs = []
    for static, dynamic, game, z, turn in parts:
        _, v = net.forward_rows([static[g] for g in game], dynamic)
        vs.append(np.asarray(v, np.float64).reshape(-1))
    V = np.concatenate(vs)
    values[name] = V
    hi = V >= 0.5
    print(
        f"{name}: MSE {np.mean((V - Z) ** 2):.4f}  turn75+ MSE {np.mean((V[late] - Z[late]) ** 2):.4f} "
        f"bias {V[late].mean() - Z[late].mean():+.4f}  acc {np.mean(np.sign(V) == np.sign(Z)):.1%}  "
        f"v>=0.5: v {V[hi].mean():+.3f} z {Z[hi].mean():+.3f} n {hi.sum()}"
    )

rng = np.random.default_rng(0)
draws = rng.integers(0, n_games, size=(2000, n_games))
base_name = sys.argv[1].split("=", 1)[0]
B = values[base_name]


def per_game(x, mask):
    s = np.bincount(G[mask], weights=x[mask], minlength=n_games)
    c = np.bincount(G[mask], minlength=n_games).astype(np.float64)
    return s, c


for name, V in values.items():
    if name == base_name:
        continue
    all_mask = np.ones(len(Z), bool)
    dse = (V - Z) ** 2 - (B - Z) ** 2
    s, c = per_game(dse, all_mask)
    ls, lc = per_game(V - B, late)  # bias change = mean v change (z is the same)
    mse_d = s.sum() / c.sum()
    bias_d = ls.sum() / lc.sum()
    boot_m = np.array([s[d].sum() / c[d].sum() for d in draws])
    boot_b = np.array([ls[d].sum() / lc[d].sum() for d in draws])
    print(
        f"{name} vs {base_name}: MSE {mse_d:+.4f} [{np.percentile(boot_m, 2.5):+.4f}, {np.percentile(boot_m, 97.5):+.4f}]  "
        f"turn75+ bias {bias_d:+.4f} [{np.percentile(boot_b, 2.5):+.4f}, {np.percentile(boot_b, 97.5):+.4f}]"
    )
