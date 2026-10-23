"""Self-play: how often the opponent (of the player to move) holds unplayed
dev cards late, and the search value's bias in each slice. Compare with live
(late_value_set): person holds 1+ dev cards in 9129/9506 positions at turn 75+.
Usage: sp_devs.py ROUND_DIR [TURN]"""

import glob
import sys

import numpy as np

from catanatron.colonist.encode import _BOARD_SIZE, PLAYER_SIZE, MY_EXTRA

OPP = _BOARD_SIZE + PLAYER_SIZE + MY_EXTRA
ARMY, DEVS = OPP + 1, OPP + 9
turn_min = int(sys.argv[2]) if len(sys.argv) > 2 else 75

from catanatron.models.enums import DEVELOPMENT_CARDS

OWN = _BOARD_SIZE + PLAYER_SIZE + 2  # own dev cards by kind, /3
own = []
devs, army, v, z, games = [], [], [], [], []
for i, f in enumerate(
    sorted(
        glob.glob(sys.argv[1] + "/data/shard_*.npz")
        + glob.glob(sys.argv[1] + "/shard_*.npz")
    )
):
    d = np.load(f)
    m = d["turn"] >= turn_min
    dyn = d["dynamic"][m].astype(np.float32)
    devs.append(np.rint(dyn[:, DEVS] * 5))
    army.append(dyn[:, ARMY] > 0.5)
    own.append(np.rint(dyn[:, OWN : OWN + len(DEVELOPMENT_CARDS)] * 3))
    v.append(d["v"][m].astype(np.float32))
    z.append(d["z"][m].astype(np.float32))
    games.append(d["game"][m].astype(np.int64) + i * 10**6)
devs, army, v, z, games = map(np.concatenate, (devs, army, v, z, games))
n = len(devs)
own = np.concatenate(own)
print(
    "own held cards by kind (mean per position): "
    + ", ".join(f"{c} {m:.2f}" for c, m in zip(DEVELOPMENT_CARDS, own.mean(0)))
)
print(f"positions at turn {turn_min}+: {n}, games {len(np.unique(games))}")
print(f"opp dev cards: mean {devs.mean():.2f}")
for k in range(5):
    print(
        f"  {k}{'+' if k == 4 else ''}: {np.mean(devs >= 4 if k == 4 else devs == k):.1%}"
    )


def row(name, m):
    if not m.any():
        print(f"{name}: n 0")
        return
    hi = m & (v > 0.5)
    imp = (v[hi].mean() + 1) / 2 if hi.any() else float("nan")
    act = (z[hi] > 0).mean() if hi.any() else float("nan")
    print(
        f"{name}: n {m.sum()} ({m.mean():.1%}) bias {np.mean(v[m] - z[m]):+.3f}"
        f" | v>0.5 n {hi.sum()} implied {imp:.1%} actual {act:.1%}"
    )


row("all", np.ones(n, bool))
row("opp no dev card", devs == 0)
row("opp dev cards", devs > 0)
row("opp 2+ dev cards", devs >= 2)
row("opp army + dev cards", army & (devs > 0))
