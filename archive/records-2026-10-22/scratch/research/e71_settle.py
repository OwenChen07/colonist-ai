"""Task 3 lead from E70: people keep about 0.5 more settlements on the board.
Is that a people style (in wins and losses alike) or a comeback signature
(only when they win)? Pieces at the end for both sides of every live game,
split by opponent band and by who won.

    .venv/bin/python data/orchestrator/scratch/research/e71_settle.py
"""

import json
import sys

sys.path.insert(0, "data/orchestrator/scratch/research")
sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from e70_stats import ci  # noqa: E402
from lvlib import load_rows  # noqa: E402

idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "ok":
            idx[r["capture"]] = r
cap = {}
for a in ("bot3", "bot4", "bot5", "bot7", "bot9"):
    for line in open(f"data/{a}/games.jsonl"):
        if line.strip():
            g = json.loads(line)
            cap[(a, g["at"])] = g

groups = {}
for r in load_rows():
    g = cap.get((r["a"], r["at"])) or {}
    x = idx.get(g.get("capture"))
    if not x or not x.get("human") or not x.get("bot"):
        continue
    mmr = r["opp_mmr"]
    band = "unrated" if mmr is None else ("2100+" if mmr >= 2100 else "<2100")
    key = (band, "bot won" if r["won"] else "person won")
    groups.setdefault(key, []).append((x["human"], x["bot"], x.get("rolls")))

for key in sorted(groups):
    rows = groups[key]
    print(f"--- {key[0]}, {key[1]}: {len(rows)} games")
    for who, k in (("person", 0), ("bot   ", 1)):
        parts = [
            f"{f} {ci([s[k].get(f) for s in rows])}"
            for f in ("settlements", "cities", "roads")
        ]
        print(f"  {who} " + "; ".join(parts))
    print(f"  rolls {ci([s[2] for s in rows])}")
