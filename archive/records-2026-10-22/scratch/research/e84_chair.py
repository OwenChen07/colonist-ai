"""E84 check: on the late self-play games (E84 step 1's data), the net's
stated win chance minus the result, split by the chair: the colour the bot
had in the live game the start came from, vs the person's colour.

Hypothesis: the bot's chair is over-rated (about +5) and the person's chair
under-rated by as much, so the two cancel in training's rows.

    .venv/bin/python data/orchestrator/scratch/research/e84_chair.py NET [MAXSHARDS]
"""

import glob
import json
import os
import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.starts import load as load_starts
from catanatron.colonist.starts import start_game

D = "data/orchestrator/e84"


def boot(xs, n=500):
    xs = np.asarray(xs)
    rng = np.random.default_rng(1)
    bs = np.sort([100 * xs[rng.integers(0, len(xs), len(xs))].mean() for _ in range(n)])
    return f"{100 * xs.mean():+5.1f} [{bs[12]:+.1f}, {bs[487]:+.1f}]"


def bot_colour(key):
    rec = convert(load_capture(os.path.join("captures", key))).record
    you = [c for c, p in rec["players"].items() if p["you"]]
    return you[0] if len(you) == 1 else None


def main():
    net = Net.load(sys.argv[1])
    starts = load_starts(f"{D}/late_starts.jsonl.gz")
    keys = json.load(open(f"{D}/late_starts.jsonl.gz.keys"))["lines"]
    assert len(keys) == len(starts)
    files = sorted(glob.glob(f"{D}/data/shard_*.npz"))
    random.Random(1).shuffle(files)
    files = files[: int(sys.argv[2]) if len(sys.argv) > 2 else 12]
    cache, out, match = {}, {}, [0, 0]
    for fi, f in enumerate(files):
        s = np.load(f)
        turn, game, z = s["turn"], s["game"], s["z"]
        for g, seed in enumerate(s["seed"]):
            rows = np.flatnonzero(game == g)
            if not len(rows) or z[rows[0]] == 0:
                continue
            rng = random.Random(int(seed) ^ 0x57A27)
            rng.random()
            idx = rng.randrange(len(starts))
            if idx not in cache:
                cache[idx] = bot_colour(keys[idx])
            bot = cache[idx]
            if bot is None:
                continue
            # The first row's colour: the start position encoded for each
            # colour, against the row's features.
            sg = start_game(starts[idx], int(seed))
            d0 = s["dynamic"][rows[0]].astype(np.float32)
            diffs = {
                c.value: float(np.abs(E.encode(sg, c)[1] - d0).sum())
                for c in sg.state.colors
            }
            first = min(diffs, key=diffs.get)
            match[0] += min(diffs.values()) < 0.3 * max(diffs.values())
            match[1] += 1
            z0 = z[rows[0]]
            for i in ([rows[0]] if len(sys.argv) > 3 else rows[::4]):
                if not 50 <= turn[i] <= 90:
                    continue
                colour = first if z[i] == z0 else next(c for c in diffs if c != first)
                _, v = net.forward(
                    s["static"][g], s["dynamic"][i][None, :].astype(np.float32)
                )
                p = (float(np.asarray(v).reshape(-1)[0]) + 1) / 2
                chair = "bot's chair" if colour == bot else "person's chair"
                out.setdefault(chair, []).append((p, (z[i] + 1) / 2, fi * 1000 + g))
        print(f"  shard {fi + 1}/{len(files)}", file=sys.stderr, flush=True)
    print(
        f"{sys.argv[1]}, {len(files)} shards, turns 50-90; first row clearly matched {match[0]}/{match[1]}"
    )
    for chair, rows in sorted(out.items()):
        st = 100 * np.mean([r[0] for r in rows])
        wr = 100 * np.mean([r[1] for r in rows])
        print(
            f"  {chair:14s}: n {len(rows)} (games {len(set(r[2] for r in rows))}) "
            f"stated {st:.1f} won {wr:.1f} gap {boot([r[0] - r[1] for r in rows])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
