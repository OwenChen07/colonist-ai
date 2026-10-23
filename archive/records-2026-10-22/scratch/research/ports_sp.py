"""Ports in self-play: how many port kinds each side holds at the end of a game,
for winners and losers, and the win rate by ports held. Read from the shards'
own-player port features (encode._player_block, `me` block).

    .venv/bin/python data/orchestrator/scratch/research/ports_sp.py runs/g1/round_190/data
"""

import glob
import sys
from collections import defaultdict

import numpy as np

from catanatron.colonist import encode as E

BOARD = E.NUM_TILES + E.NUM_NODES * 4 + E.NUM_EDGES * 2
PORTS_AT = BOARD + 10 + 5 + 4 + 5 + 5  # me block: base, totals, hand, played, prod x2
PHASE_AT = BOARD + E.PLAYER_SIZE * 2 + E.MY_EXTRA


def main(dirs):
    ends = []  # (z, n_ports, n_two, turn, after_setup_ports)
    for d in dirs:
        for path in sorted(glob.glob(f"{d}/shard_*.npz")):
            s = np.load(path)
            game, z, turn = s["game"], s["z"], s["turn"]
            dyn = s["dynamic"].astype(np.float32)
            ports = dyn[:, PORTS_AT : PORTS_AT + 6] > 0.5
            nodes = E.NUM_TILES + np.arange(E.NUM_NODES) * 4
            spots = (dyn[:, nodes] > 0.5).sum(1) + (dyn[:, nodes + 1] > 0.5).sum(1)
            setup = dyn[:, PHASE_AT] > 0.5
            excl = s["human_opponent"]
            # one side's rows in a game share z; key by (game, z)
            last, first_play = {}, {}
            for i in range(len(game)):
                g = int(game[i])
                if excl[g] or z[i] == 0:
                    continue
                k = (g, int(z[i]))
                if k not in last or turn[i] >= turn[last[k]]:
                    last[k] = i
                if not setup[i] and (
                    k not in first_play or turn[i] < turn[first_play[k]]
                ):
                    first_play[k] = i
            for k, i in last.items():
                p = ports[i]
                f = first_play.get(k)
                ends.append(
                    (
                        k[1],
                        int(p.sum()),
                        int(p[:5].sum()),
                        int(turn[i]),
                        -1 if f is None else int(ports[f].sum()),
                        int(spots[i]),
                    )
                )
    a = np.array(ends)
    print(f"sides read: {len(a)} (games with both sides: about {len(a) // 2})")
    for z, name in ((1, "winners"), (-1, "losers")):
        b = a[a[:, 0] == z]
        print(
            f"{name}: {len(b)}; port kinds at the end {b[:, 1].mean():.2f} "
            f"(2:1 {b[:, 2].mean():.2f}); ports after setup {b[b[:, 4] >= 0][:, 4].mean():.2f}; "
            f"share with 0 ports {np.mean(b[:, 1] == 0):.1%}"
        )
    print("win rate by port kinds at the end:")
    for n in range(0, 4):
        sel = a[:, 1] == n if n < 3 else a[:, 1] >= 3
        m = sel.sum()
        if m:
            w = np.mean(a[sel, 0] == 1)
            print(
                f"  {n}{'+' if n == 3 else ''}: {m} sides, win {w:.1%} ± {1.96 * np.sqrt(w * (1 - w) / m):.1%}"
            )
    print("win rate, port vs no port, at the same number of building spots:")
    for n in range(2, 8):
        sel = a[:, 5] == n
        for has, label in ((a[:, 1] == 0, "no port"), (a[:, 1] > 0, "port")):
            b = a[sel & has]
            if len(b):
                w = np.mean(b[:, 0] == 1)
                print(
                    f"  {n} spots, {label}: {len(b)} sides, win {w:.1%} ± {1.96 * np.sqrt(w * (1 - w) / len(b)):.1%}"
                )
    print("by game length (last turn of the side):")
    for lo, hi in ((0, 60), (60, 80), (80, 100), (100, 400)):
        sel = (a[:, 3] >= lo) & (a[:, 3] < hi)
        b = a[sel]
        if len(b):
            wpt = b[b[:, 0] == 1][:, 1].mean() if (b[:, 0] == 1).any() else float("nan")
            lpt = (
                b[b[:, 0] == -1][:, 1].mean() if (b[:, 0] == -1).any() else float("nan")
            )
            print(
                f"  turns {lo}-{hi}: {len(b)} sides; winners {wpt:.2f} ports, losers {lpt:.2f}"
            )


if __name__ == "__main__":
    main(sys.argv[1:])
