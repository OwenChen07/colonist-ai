"""Paired r69 - r66 value difference on the same mid-game positions."""

import json
import random

a = json.load(open("data/orchestrator/live-loss/mid_bias_rows.json"))
b = json.load(open("data/orchestrator/live-loss/mid_bias_rows_r69.json"))
diffs = [[y["v"] - x["v"] for x, y in zip(ga, gb)] for ga, gb in zip(a, b)]
diffs = [d for d in diffs if d]
flat = [x for d in diffs for x in d]
rng = random.Random(0)
vals = []
for _ in range(1000):
    s = [x for _ in diffs for x in diffs[rng.randrange(len(diffs))]]
    vals.append(sum(s) / len(s))
vals.sort()
print(
    f"r69-r66 mean v: {sum(flat) / len(flat):+.4f} [{vals[25]:+.4f}, {vals[974]:+.4f}] (n {len(flat)}, g {len(diffs)})"
)
won = sum(1 for g in a if g and g[0]["z"] > 0) / sum(1 for g in a if g)
print(f"bot won {won:.3f} of {sum(1 for g in a if g)} games")
