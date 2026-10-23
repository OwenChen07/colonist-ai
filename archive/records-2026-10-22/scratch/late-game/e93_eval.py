"""E93 check: on the people holdout (data/human/holdout/value: our live games,
the bot's chair, real result), each net's stated chance minus the result and
its value error, by opponent band and game turn; and the differences of each
net from the control, paired by game (bootstrap over games, 2,000 draws).

    .venv/bin/python data/orchestrator/scratch/late-game/e93_eval.py [name=path ...]
Default nets: step 50, E85 step 1 (control), E93 if its net exists.
"""

import glob
import json
import os
import sys

import numpy as np

from catanatron.colonist.net import Net

H = "data/human"
NETS = {
    "step50": "data/orchestrator/e54/run1/iter-50/net.npz",
    "e85s1": "data/orchestrator/e85/net.npz",
    "e93": "data/orchestrator/e93/net.npz",
}
CONTROL = "e85s1"
BANDS = [
    ("under 2100", 0, 2100),
    ("2100-2300", 2100, 2300),
    ("2300+", 2300, 9999),
    ("2100+", 2100, 9999),
]
TURNS = [("all", 0, 999), ("t<20", 0, 20), ("t20-39", 20, 40), ("t40+", 40, 999)]


def ratings():
    peak = {
        g["cap"]: g.get("mmr")
        for g in json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
    }
    old = json.load(open("data/orchestrator/e66/ratings.json"))
    cap_of = {}
    for line in open(f"{H}/index.jsonl"):
        r = json.loads(line)
        if r.get("status") == "ok" and "game" in r:
            cap_of[f"{int(r['game']):019d}"] = r["capture"]
    out = {}
    for s in set(cap_of) | set(old):
        m = peak.get(cap_of.get(s))
        out[s] = m if m is not None else (old.get(s) or {}).get("mmr")
    return out


def load(paths):
    games = []
    for p in paths:
        with np.load(p) as d:
            games.append(
                (
                    d["static"].astype(np.float32),
                    d["dynamic"].astype(np.float32),
                    d["game"].astype(np.int64),
                    d["z"].astype(np.float64),
                    d["turn"].astype(np.int64),
                )
            )
    return games


def values(net, games):
    out = []
    for static, dyn, game, _, _ in games:
        _, v = net.forward_rows([static[g] for g in game], dyn)
        out.append(np.asarray(v, np.float64))
    return out


def per_game(v, games, lo, hi):
    """Per game: (sum of stated-minus-won in points, sum sq err, n rows)."""
    rows = []
    for vals, (_, _, _, z, turn) in zip(v, games):
        m = (turn >= lo) & (turn < hi)
        if not m.any():
            rows.append((0.0, 0.0, 0))
            continue
        gap = 50 * (vals[m] - z[m])  # (v+1)/2 - (z+1)/2, in points
        rows.append((gap.sum(), ((vals[m] - z[m]) ** 2).sum(), int(m.sum())))
    return np.array(rows)


def ci(num, den, rng):
    keep = den > 0
    num, den = num[keep], den[keep]
    idx = rng.integers(0, len(num), (2000, len(num)))
    boot = num[idx].sum(1) / den[idx].sum(1)
    return num.sum() / den.sum(), np.percentile(boot, 2.5), np.percentile(boot, 97.5)


def main():
    nets = dict(NETS)
    for a in sys.argv[1:]:
        k, p = a.split("=", 1)
        nets[k] = p
    nets = {k: Net.load(p) for k, p in nets.items() if os.path.exists(p)}
    rate = ratings()
    paths = sorted(glob.glob(f"{H}/holdout/value/shard_*.npz"))
    mmr = {p: rate.get(os.path.basename(p).split("_")[1]) for p in paths}
    rng = np.random.default_rng(0)
    for band, lo, hi in BANDS:
        sel = [p for p in paths if mmr[p] is not None and lo <= mmr[p] < hi]
        games = load(sel)
        vals = {k: values(n, games) for k, n in nets.items()}
        print(
            f"\n== {band}: {len(sel)} games, {sum(len(g[3]) for g in games)} positions"
        )
        for tname, tlo, thi in TURNS:
            pg = {k: per_game(v, games, tlo, thi) for k, v in vals.items()}
            line = []
            for k, a in pg.items():
                g, glo, ghi = ci(a[:, 0], a[:, 2], rng)
                mse = a[:, 1].sum() / max(a[:, 2].sum(), 1)
                line.append(f"{k} gap {g:+.1f} [{glo:+.1f},{ghi:+.1f}] mse {mse:.3f}")
            print(f"  {tname:7s} " + " | ".join(line))
            if CONTROL in pg:
                c = pg[CONTROL]
                for k, a in pg.items():
                    if k == CONTROL:
                        continue
                    d, dlo, dhi = ci(a[:, 0] - c[:, 0], c[:, 2], rng)
                    e, elo, ehi = ci(a[:, 1] - c[:, 1], c[:, 2], rng)
                    print(
                        f"          {k}-{CONTROL}: gap {d:+.1f} [{dlo:+.1f},{dhi:+.1f}]"
                        f" mse {e:+.4f} [{elo:+.4f},{ehi:+.4f}]"
                    )


if __name__ == "__main__":
    main()
