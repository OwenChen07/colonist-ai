"""Roads, settlements and cities per player at each self-play game's end,
by round (for live-loss's question: did E59 shift road-vs-city play?).
A game's last recorded row stands for its end (the last move is not in it)."""

import glob
import sys

import numpy as np

from catanatron.colonist import encode as E

ROOT = "/home/owen/colonist-ai/runs/g1"
SHARDS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
NODES = E._NODES_AT
ROADS = E._ROADS_AT
N, M = E.NUM_NODES, E.NUM_EDGES

print("round games turns | winner: roads setl city | loser: roads setl city")
for r in range(128, 157):
    files = sorted(glob.glob(f"{ROOT}/round_{r:03d}/data/shard_*.npz"))[:SHARDS]
    if not files:
        continue
    rows = []
    for f in files:
        d = np.load(f)
        game, z, turn = d["game"], d["z"], d["turn"]
        hum = d["human_opponent"] if "human_opponent" in d.files else None
        dyn = d["dynamic"]
        last = np.r_[np.nonzero(np.diff(game))[0], len(game) - 1]
        for i in last:
            g = game[i]
            if hum is not None and hum[g]:
                continue
            if z[i] == 0:
                continue
            nodes = dyn[i, NODES : NODES + 4 * N].reshape(N, 4).astype(bool)
            roads = dyn[i, ROADS : ROADS + 2 * M].reshape(M, 2).astype(bool)
            me = (roads[:, 0].sum(), nodes[:, 0].sum(), nodes[:, 1].sum())
            them = (roads[:, 1].sum(), nodes[:, 2].sum(), nodes[:, 3].sum())
            win, lose = (me, them) if z[i] == 1 else (them, me)
            rows.append((turn[i], *win, *lose))
    a = np.array(rows, float)
    m = a.mean(0)
    print(
        f"{r:5d} {len(a):5d} {m[0]:5.1f} | {m[1]:5.2f} {m[2]:4.2f} {m[3]:4.2f}"
        f" | {m[4]:5.2f} {m[5]:4.2f} {m[6]:4.2f}"
    )
    sys.stdout.flush()
