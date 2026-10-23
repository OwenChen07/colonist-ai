"""Where is the raw net too hopeful against people? Ranked games (data/ranked/bot_value),
positions at turn 20+ with the net's value in [-0.3, 0.3): won - stated, split by simple
features. Intervals are game-clustered (+-1.96 se over per-game sums).
Usage: gap_split.py NET"""

import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net

net = Net.load(sys.argv[1])
parts = load_value_shards(
    [
        (
            sys.argv[2]
            if len(sys.argv) > 2
            else "/home/owen/colonist-ai/data/ranked/bot_value"
        )
    ]
)
B = E._BOARD_SIZE
V, Z, T, G, D = [], [], [], [], []
for i, (static, dynamic, game, z, turn) in enumerate(parts):
    _, v = net.forward_rows([static[g] for g in game], dynamic)
    V.append(np.asarray(v, np.float64)), Z.append(z), T.append(turn), G.append(
        np.full(len(z), i)
    )
    D.append(dynamic)
V, Z, T, G, D = (np.concatenate(x) for x in (V, Z, T, G, D))
me, op = B, B + 46
feat = {
    "public VP diff (me-them)": np.round(D[:, me] * 15 - D[:, op] * 15),
    "my hidden VP cards": np.round(D[:, me + 35] * 15 - D[:, me] * 15),
    "their dev cards": np.round(D[:, op + 9] * 5),
    "my dev cards": np.round(D[:, me + 9] * 5),
    "their hand": np.round(D[:, op + 8] * 10),
    "my hand": np.round(D[:, me + 8] * 10),
    "army: me / them / none": np.where(
        D[:, me + 1] > 0, 1, np.where(D[:, op + 1] > 0, -1, 0)
    ),
    "road: me / them / none": np.where(
        D[:, me + 2] > 0, 1, np.where(D[:, op + 2] > 0, -1, 0)
    ),
    "their VP (public)": np.round(D[:, op] * 15),
    "my turn": D[:, B + 81 + 6],
}
sel = (T >= 20) & (V >= -0.3) & (V < 0.3)
print(f"{len(parts)} games; selected {sel.sum()} positions (turn 20+, v in [-0.3,0.3))")
won, stated = (Z > 0).astype(float), (V + 1) / 2


def row(label, m):
    games = np.unique(G[m])
    if m.sum() < 200 or len(games) < 30:
        return
    # clustered: per-game sums of the residual, ratio estimator
    r = np.array([(won[m & (G == g)] - stated[m & (G == g)]).sum() for g in games])
    n = np.array([(m & (G == g)).sum() for g in games])
    gap = r.sum() / n.sum()
    se = np.sqrt(np.sum((r - gap * n) ** 2) * len(games) / (len(games) - 1)) / n.sum()
    print(
        f"  {label:>8}: n {m.sum():6d} games {len(games):4d} stated {stated[m].mean():.1%} "
        f"won {won[m].mean():.1%} gap {gap:+.1%} +-{1.96 * se:.1%}"
    )


row("all", sel)
for name, x in feat.items():
    print(name)
    vals = np.unique(x[sel])
    for val in vals:
        row(f"{val:g}", sel & (x == val))
