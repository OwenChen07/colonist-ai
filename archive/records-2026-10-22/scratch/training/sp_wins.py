"""Win rate and game length of the recorded (main net) seat in self-play shards.
Usage: sp_wins.py DIR [DIR ...]
Only valid when one seat is recorded per game (human-seat games): shards have
no colour column, so in two-seat self-play this reports the last mover's z."""

import glob
import sys

import numpy as np

for d in sys.argv[1:]:
    wins, turns = [], []
    files = sorted(glob.glob(d + "/data/shard_*.npz") + glob.glob(d + "/shard_*.npz"))
    for f in files:
        s = np.load(f)
        g, z, t = s["game"], s["z"], s["turn"]
        for k in np.unique(g):
            m = g == k
            wins.append(z[m][-1] > 0)
            turns.append(t[m].max())
    wins, turns = np.array(wins), np.array(turns)
    print(
        f"{d}: games {len(wins)}, win {wins.mean():.1%}, "
        f"last turn median {np.median(turns):.0f}, mean {turns.mean():.0f}"
    )
