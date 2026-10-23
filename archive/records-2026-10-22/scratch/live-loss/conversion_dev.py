"""Within the bot's own games: does a higher dev-card share of its rolls 20-59 cards
go with fewer wins, at the same production gap? Reads conversion.json."""

import json
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from long_games import wil  # noqa: E402

res = json.load(open("data/orchestrator/live-loss/conversion.json"))
rows = []
for r in res:
    b, p = r["t"]["mid"]["bot"], r["t"]["mid"]["person"]
    if b["prod"] <= 0 or p["prod"] <= 0:
        continue
    rows.append((-b["dev"] / b["prod"], b["prod"] - p["prod"], r["won"]))
rows.sort()
n = len(rows)
cuts = [rows[n // 3][0], rows[2 * n // 3][0]]
print(f"{n} games; dev share tertile cuts {cuts[0]:.2f} / {cuts[1]:.2f}")
gaps = sorted(x[1] for x in rows)
g3 = [gaps[n // 3], gaps[2 * n // 3]]
print(f"production gap tertile cuts {g3[0]:+.1f} / {g3[1]:+.1f} cards")


def band(v, c):
    return 0 if v < c[0] else (1 if v < c[1] else 2)


for gb, gname in enumerate(("behind", "even", "ahead")):
    line = []
    for db, dname in enumerate(("low dev", "mid dev", "high dev")):
        sel = [w for s, g, w in rows if band(g, g3) == gb and band(s, cuts) == db]
        line.append(f"{dname} {wil(sum(sel), len(sel))} ({len(sel)})")
    print(f"  production {gname:6}: " + "; ".join(line))
for db, dname in enumerate(("low dev", "mid dev", "high dev")):
    sel = [w for s, g, w in rows if band(s, cuts) == db]
    print(f"  all {dname}: {wil(sum(sel), len(sel))} ({len(sel)})")
