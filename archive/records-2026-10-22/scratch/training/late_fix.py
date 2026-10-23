"""Can a per-turn affine value fix (v' = a*v + b), fitted on live train games,
cut the live value error on live holdout games? Usage: NET TRAIN_DIR HOLDOUT_DIR"""

import sys

import numpy as np

from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net

net = Net.load(sys.argv[1])
BUCKETS = ((0, 20), (20, 40), (40, 60), (60, 75), (75, 90), (90, 10**6))


def rows(folder):
    T, V, Z = [], [], []
    for static, dynamic, game, z, turn in load_value_shards([folder]):
        _, v = net.forward_rows([static[g] for g in game], dynamic)
        V.append(np.asarray(v, np.float64).reshape(-1))
        Z.append(np.asarray(z, np.float64))
        T.append(np.asarray(turn, np.float64))
    return np.concatenate(T), np.concatenate(V), np.concatenate(Z)


tr = rows(sys.argv[2])
ho = rows(sys.argv[3])
print(f"train {len(tr[0])} positions, holdout {len(ho[0])}")
fits = {}
for lo, hi in BUCKETS:
    T, V, Z = tr
    m = (T >= lo) & (T < hi)
    a, b = np.polyfit(V[m], Z[m], 1)  # least squares z ~ a*v + b
    s = float(np.sum(V[m] * Z[m]) / np.sum(V[m] ** 2))  # scale only, z ~ s*v
    fits[(lo, hi)] = (a, b, s)
tot = {"raw": 0.0, "affine": 0.0, "scale": 0.0}
n = 0
print("turn    n      raw MSE  affine  scale | a     b      s   | bias raw  acc")
for (lo, hi), (a, b, s) in fits.items():
    T, V, Z = ho
    m = (T >= lo) & (T < hi)
    raw = np.mean((V[m] - Z[m]) ** 2)
    aff = np.mean((np.clip(a * V[m] + b, -1, 1) - Z[m]) ** 2)
    sc = np.mean((np.clip(s * V[m], -1, 1) - Z[m]) ** 2)
    k = int(m.sum())
    tot["raw"] += raw * k
    tot["affine"] += aff * k
    tot["scale"] += sc * k
    n += k
    acc = np.mean(np.sign(V[m]) == np.sign(Z[m]))
    print(
        f"{lo:3d}-{min(hi, 999):<3d} {k:6d}  {raw:.3f}   {aff:.3f}   {sc:.3f} | "
        f"{a:.3f} {b:+.3f} {s:.3f} | {V[m].mean() - Z[m].mean():+.3f}  {acc:.1%}"
    )
print({k: round(v / n, 4) for k, v in tot.items()})
