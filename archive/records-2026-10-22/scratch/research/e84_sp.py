"""E84 control: the net's stated win chance minus the result on self-play
positions (a round's shards), turns 50-90, split by games from colonist's
own starts vs the empty board.

    .venv/bin/python data/orchestrator/scratch/research/e84_sp.py NET ROUND_DIR [MAXSHARDS]
"""

import glob
import random
import sys

import numpy as np

from catanatron.colonist.net import Net


def boot(xs, n=500):
    xs = np.asarray(xs)
    rng = np.random.default_rng(1)
    bs = np.sort([100 * xs[rng.integers(0, len(xs), len(xs))].mean() for _ in range(n)])
    return f"{100 * xs.mean():+5.1f} [{bs[12]:+.1f}, {bs[487]:+.1f}]"


def main():
    net = Net.load(sys.argv[1])
    files = sorted(glob.glob(f"{sys.argv[2]}/data/shard_*.npz"))
    random.Random(1).shuffle(files)
    files = files[: int(sys.argv[3]) if len(sys.argv) > 3 else 12]
    out = {}
    for f in files:
        s = np.load(f)
        turn, game, z = s["turn"], s["game"], s["z"]
        hs = s["human_start"][game]
        keep = (turn >= 50) & (turn <= 90)
        for i in np.flatnonzero(keep)[::4]:  # every 4th row: enough, and fast
            _, v = net.forward(
                s["static"][game[i]], s["dynamic"][i][None, :].astype(np.float32)
            )
            p = (float(np.asarray(v).reshape(-1)[0]) + 1) / 2
            out.setdefault(bool(hs[i]), []).append(
                (p, (z[i] + 1) / 2, game[i] + 10_000 * files.index(f))
            )
    print(f"{sys.argv[1]} on {sys.argv[2]}, {len(files)} shards, turns 50-90")
    for hs, rows in sorted(out.items()):
        st = 100 * np.mean([r[0] for r in rows])
        wr = 100 * np.mean([r[1] for r in rows])
        print(
            f"  {'people start' if hs else 'empty board '}: n {len(rows)} (games {len(set(r[2] for r in rows))}) "
            f"stated {st:.1f} won {wr:.1f} gap {boot([r[0] - r[1] for r in rows])}"
        )
        for lo in (0, 0.2, 0.4, 0.6, 0.8):
            sel = [
                r for r in rows if lo <= r[0] < lo + 0.2 or (lo == 0.8 and r[0] >= 1)
            ]
            if sel:
                print(
                    f"    {lo:.1f}-{lo + 0.2:.1f}: n {len(sel):5d} stated {100 * np.mean([r[0] for r in sel]):5.1f} won {100 * np.mean([r[1] for r in sel]):5.1f}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
