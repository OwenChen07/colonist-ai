"""Simulations our live search used at each setup settlement, by seat and pick."""

import collections
import glob
import json
import statistics as st

by = collections.defaultdict(list)
sample = None
for path in glob.glob("data/openhex/games/*.json"):
    g = json.load(open(path))
    if not g.get("turn_order") or not g.get("me"):
        continue
    seat = "1st" if g["turn_order"][0] == g["me"] else "2nd"
    setl = [m for m in g.get("moves", []) if "SETTLEMENT" in str(m[0])][:2]
    for i, m in enumerate(setl):
        if isinstance(m[1], int):
            by[(seat, i)].append(m[1])
    if sample is None and len(g.get("moves", [])) > 3:
        sample = g["moves"][:4]
for k in sorted(by):
    v = by[k]
    print(k, "n", len(v), "median", st.median(v), "min", min(v), "max", max(v))
print("sample moves", sample)
