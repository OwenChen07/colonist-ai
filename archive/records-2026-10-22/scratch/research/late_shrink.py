"""E49 step 3: is the late live error a scale (calibration) problem or a ranking problem?
Usage: late_shrink.py NET ...
For turns >= 60 on the live holdout: raw MSE, the best scalar shrink c (v -> c v) and its
MSE, and the AUC of v for the bot's win (unchanged by any monotone rescale)."""

import sys

import numpy as np

from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net


def auc(score, win):
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    pos = win.sum()
    neg = len(win) - pos
    return (ranks[win].sum() - pos * (pos + 1) / 2) / (pos * neg)


parts = load_value_shards(["/home/owen/colonist-ai/data/human/holdout/value"])
for path in sys.argv[1:]:
    net = Net.load(path)
    V, Z, T = [], [], []
    for static, dynamic, game, z, turn in parts:
        _, v = net.forward_rows([static[g] for g in game], dynamic)
        V.append(np.asarray(v, np.float64)), Z.append(z.astype(np.float64)), T.append(
            turn
        )
    v, z, t = np.concatenate(V), np.concatenate(Z), np.concatenate(T)
    out = []
    for lo, hi in ((60, 75), (75, 90), (90, 10**6), (60, 10**6)):
        m = (t >= lo) & (t < hi)
        c = (v[m] * z[m]).sum() / (v[m] ** 2).sum()
        out.append(
            f"t{lo}-{hi if hi < 10**6 else ''} n {m.sum()} mse {np.mean((v[m] - z[m]) ** 2):.3f} "
            f"c {c:.2f} -> {np.mean((c * v[m] - z[m]) ** 2):.3f} auc {auc(v[m], z[m] > 0):.4f}"
        )
    print(path + ":\n  " + "\n  ".join(out), flush=True)
