"""Fix 1 sizing: our live games' value shards (data/human/train/value) by the
opponent's rating, their positions, and the self-play window's positions.
Ratings: peak_story.json (by capture), else e66/ratings.json (by game seed).
Writes the 2100+ train shard list to data/orchestrator/e93/train2100.txt."""

import glob
import json
import os

import numpy as np

H = "data/human"
peak = {
    g["cap"]: g.get("mmr")
    for g in json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
}
old = json.load(open("data/orchestrator/e66/ratings.json"))


def rows(path):
    with np.load(path) as z:
        return len(z["game"])


def seed(path):
    return os.path.basename(path).split("_")[1]


cap_of = {}
for line in open(f"{H}/index.jsonl"):
    r = json.loads(line)
    if r.get("status") == "ok" and "game" in r:
        cap_of[f"{int(r['game']):019d}"] = r["capture"]

for split in ("train", "holdout"):
    paths = sorted(glob.glob(f"{H}/{split}/value/shard_*_value.npz"))
    bands = {"<2100": [0, 0], "2100-2300": [0, 0], "2300+": [0, 0], "none": [0, 0]}
    hi = []
    for p in paths:
        s = seed(p)
        m = peak.get(cap_of.get(s))
        if m is None:
            m = (old.get(s) or {}).get("mmr")
        n = rows(p)
        b = (
            "none"
            if m is None
            else "<2100" if m < 2100 else "2100-2300" if m < 2300 else "2300+"
        )
        bands[b][0] += 1
        bands[b][1] += n
        if m is not None and m >= 2100:
            hi.append(p)
    tot = sum(v[1] for v in bands.values())
    print(split, len(paths), "games", tot, "positions")
    for b, (g, n) in bands.items():
        print(f"  {b:10s} {g:5d} games {n:8d} positions ({100 * n / max(tot, 1):.1f}%)")
    if split == "train":
        os.makedirs("data/orchestrator/e93", exist_ok=True)
        with open("data/orchestrator/e93/train2100.txt", "w") as f:
            f.write("\n".join(os.path.abspath(p) for p in hi) + "\n")

sp = 0
for r in range(182, 200):
    fs = glob.glob(f"runs/g1/round_{r:03d}/data/shard_*.npz")
    sp += sum(rows(p) for p in fs)
print("self-play rounds 182-199 positions", sp)
