"""Simulations per searched move in our OpenHex game files (moves: [str, used])."""

import glob
import json
import os
import statistics as st

GAMES = "/home/owen/colonist-ai/data/openhex/games"
allsims = []
for path in sorted(glob.glob(f"{GAMES}/*.json"), key=os.path.getmtime):
    rec = json.load(open(path))
    sims = [m[1] for m in rec.get("moves", []) if isinstance(m[1], int) and m[1] > 1]
    forced = sum(1 for m in rec.get("moves", []) if m[1] == 1)
    net = sum(1 for m in rec.get("moves", []) if m[1] == 0)
    allsims += sims
    if len(sims) > 1:
        q = st.quantiles(sims, n=10)
        print(
            f"{rec['id'][:8]} searched {len(sims)} median {st.median(sims):.0f} "
            f"p10 {q[0]:.0f} p90 {q[-1]:.0f} under800 {sum(s < 800 for s in sims)} "
            f"forced {forced} net-only {net}"
        )
q = st.quantiles(allsims, n=10)
print(
    f"ALL searched {len(allsims)} median {st.median(allsims):.0f} p10 {q[0]:.0f} "
    f"p90 {q[-1]:.0f} under800 {sum(s < 800 for s in allsims) / len(allsims):.0%}"
)
