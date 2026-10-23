"""E27 check: in human-seat games vs plain games, the opponent's held dev cards
at turn TURN+ (seen from the recorded net seat), the search value's bias, and
net win rate. Usage: e27_check.py ROUND_DIR [TURN]"""

import glob
import sys

import numpy as np

from catanatron.colonist.encode import _BOARD_SIZE, PLAYER_SIZE, MY_EXTRA

OPP = _BOARD_SIZE + PLAYER_SIZE + MY_EXTRA
DEVS = OPP + 9
turn_min = int(sys.argv[2]) if len(sys.argv) > 2 else 60

rows = {True: [], False: []}
wins = {True: [], False: []}
lens = {True: [], False: []}
files = sorted(glob.glob(sys.argv[1] + "/data/shard_*.npz"))
for f in files:
    d = np.load(f)
    hum = d["human_opponent"]
    g = d["game"]
    t = d["turn"]
    dyn = d["dynamic"]
    for k in np.unique(g):
        m = g == k
        h = bool(hum[k])
        lens[h].append(int(t[m].max()))
        late = m & (t >= turn_min)
        if not late.any():
            continue
        devs = np.rint(dyn[late, DEVS].astype(np.float32) * 5)
        v = d["v"][late].astype(np.float32)
        z = d["z"][late].astype(np.float32)
        rows[h].append((devs, v - z))
    if hum.any():
        # z is from the mover's view; in human games only the net seat moves are
        # recorded, so the last position's z tells who won
        for k in np.flatnonzero(hum):
            m = np.flatnonzero(g == k)
            if len(m):
                wins[True].append(d["z"][m[-1]] > 0)

print(f"{len(files)} shards")
for h in (True, False):
    if not rows[h]:
        print(f"human seat {h}: none")
        continue
    devs = np.concatenate([r[0] for r in rows[h]])
    bias = np.concatenate([r[1] for r in rows[h]])
    n = len(lens[h])
    line = (
        f"human seat {h}: games {n}, median last turn {np.median(lens[h]):.0f}, "
        f"positions at turn {turn_min}+ {len(devs)}, opp dev cards held mean "
        f"{devs.mean():.2f}, held 1+ {np.mean(devs > 0):.1%}, value bias {bias.mean():+.3f}"
        f" (with opp 1+ cards {bias[devs > 0].mean():+.3f})"
    )
    if h and wins[True]:
        line += f", net wins {np.mean(wins[True]):.1%}"
    print(line)
