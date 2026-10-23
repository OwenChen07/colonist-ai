"""E49 step 2: late-game calibration of the search value in self-play, by round.
Usage: late_cal.py ROUND [ROUND ...]   (reads up to 25 shards per round, self-play games only)
Per round: games, median last turn, share of positions at turn >= 75, and at
turns 75-90 the bias mean(v) - mean(z) and, when v >= 0.5, mean v vs realised z."""

import glob
import sys

import numpy as np

for r in sys.argv[1:]:
    files = sorted(glob.glob(f"runs/g1/round_{int(r):03d}/data/shard_*.npz"))[:25]
    V, Z, T, last = [], [], [], []
    for f in files:
        s = np.load(f)
        g, z, t, v = s["game"], s["z"], s["turn"], s["v"].astype(np.float32)
        ho = (
            s["human_opponent"]
            if "human_opponent" in s.files
            else np.zeros(g.max() + 1, bool)
        )
        keep = ~ho[g]
        g, z, t, v = g[keep], z[keep], t[keep], v[keep]
        for k in np.unique(g):
            last.append(t[g == k].max())
        V.append(v), Z.append(z.astype(np.float32)), T.append(t)
    if not V:
        print(f"round {r}: no shards")
        continue
    V, Z, T, last = (
        np.concatenate(V),
        np.concatenate(Z),
        np.concatenate(T),
        np.array(last),
    )
    ok = np.isfinite(V) & (V != 0)
    late = ok & (T >= 75) & (T < 90)
    hi = late & (V >= 0.5)
    mid = late & (np.abs(V) < 0.5)
    print(
        f"round {r}: games {len(last)}, last turn median {np.median(last):.0f}, "
        f">=75 turns {np.mean(last >= 75):.1%} of games, "
        f"t75-90 n {late.sum()} bias {V[late].mean() - Z[late].mean():+.3f} "
        f"mse {np.mean((V[late] - Z[late]) ** 2):.3f}; "
        f"v>=.5: n {hi.sum()} v {V[hi].mean():+.3f} z {Z[hi].mean():+.3f}; "
        f"|v|<.5: v {V[mid].mean():+.3f} z {Z[mid].mean():+.3f}"
    )
