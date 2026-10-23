"""Rounds 73 / 152 / 168 on the human holdout, by opponent rating band:
value MSE (bot's positions, real result) by phase and human move match.
Paired game bootstrap for the differences against round 73."""

import glob
import json
import os
import re
import sys

import numpy as np

from catanatron.colonist.calibrate import calibrate, describe
from catanatron.colonist.net import Net
from catanatron.colonist.opponent_model import score

H = "/home/owen/colonist-ai/data/human/holdout"
RATINGS = json.load(open("/home/owen/colonist-ai/data/orchestrator/e66/ratings.json"))
NETS = {
    r: f"/home/owen/colonist-ai/runs/g1/round_{r:03d}/train/net.npz"
    for r in (73, 152, 168, 182, 189)
}
BANDS = [
    ("2100-2300", 2100, 2300),
    ("2300+", 2300, 9999),
    ("2100+", 2100, 9999),
    ("under 2100", 0, 2100),
    ("all rated", 0, 9999),
]


def seed_of(path):
    m = re.search(r"shard_(\d+)_", os.path.basename(path))
    return m.group(1) if m else None


def mmr(path):
    r = RATINGS.get(seed_of(path))
    return r["mmr"] if r and r.get("mmr") else None


def per_game_sq(net, paths):
    """[(sum sq err, n positions)] per shard file, and the overall calibrate()."""
    out = []
    for p in paths:
        with np.load(p) as d:
            static = d["static"].astype(np.float32)
            dyn = d["dynamic"].astype(np.float32)
            game = d["game"].astype(np.int64)
            z = d["z"].astype(np.float64)
        _, v = net.forward_rows([static[g] for g in game], dyn)
        e = (np.asarray(v, np.float64) - z) ** 2
        out.append((e.sum(), len(e)))
    return np.array(out)


def main():
    nets = {r: Net.load(p) for r, p in NETS.items()}
    vpaths = sorted(glob.glob(f"{H}/value/shard_*.npz"))
    opaths = sorted(glob.glob(f"{H}/opponent/shard_*.npz"))
    rated = sum(mmr(p) is not None for p in vpaths)
    print(f"value shards {len(vpaths)}, rated {rated}; opponent shards {len(opaths)}")
    rng = np.random.default_rng(0)
    for band, lo, hi in BANDS:
        vp = [p for p in vpaths if mmr(p) is not None and lo <= mmr(p) < hi]
        op = [p for p in opaths if mmr(p) is not None and lo <= mmr(p) < hi]
        print(f"\n== {band}: {len(vp)} games (value), {len(op)} games (moves)")
        sq = {}
        for r, net in nets.items():
            parts = []
            for p in vp:
                with np.load(p) as d:
                    parts.append(
                        (
                            d["static"].astype(np.float32),
                            d["dynamic"].astype(np.float32),
                            d["game"].astype(np.int64),
                            d["z"].astype(np.float32),
                            d["turn"].astype(np.int64),
                        )
                    )
            res = calibrate(net, parts)
            print(f"round {r}: {describe(res)}")
            sq[r] = per_game_sq(net, vp)
        base = sq[73]
        for r in (r for r in nets if r != 73):
            diffs = []
            n = len(base)
            for _ in range(2000):
                i = rng.integers(0, n, n)
                diffs.append(
                    sq[r][i, 0].sum() / sq[r][i, 1].sum()
                    - base[i, 0].sum() / base[i, 1].sum()
                )
            lo_, hi_ = np.percentile(diffs, [2.5, 97.5])
            d0 = (
                sq[r][:, 0].sum() / sq[r][:, 1].sum()
                - base[:, 0].sum() / base[:, 1].sum()
            )
            print(
                f"  mse round {r} - round 73: {d0:+.4f} (95% {lo_:+.4f} to {hi_:+.4f})"
            )
        s = score(op, {f"r{r}": n for r, n in nets.items()})
        for k, v in s.items():
            print(
                f"  moves {k}: {v['rows']} top-1 {v['accuracy']:.2%} CE {v['cross_entropy']:.4f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
