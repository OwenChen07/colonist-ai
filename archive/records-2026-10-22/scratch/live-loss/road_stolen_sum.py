"""Summary of road_stolen_positions.jsonl: disagreement and Q gaps."""

import json
from collections import Counter

rows = [
    json.loads(x)
    for x in open("data/orchestrator/live-loss/road_stolen_positions.jsonl")
]
games = {r["capture"] for r in rows}
diff = [r for r in rows if r["played"] != r["best"]]
print(
    f"games {len(games)} positions {len(rows)} differ {len(diff)} failures {sum(r['failure'] for r in rows)}"
)
gaps = sorted(r["q_best"] - r["q_played"] for r in diff if "q_played" in r)
print("gaps where differ (q_best - q_played):", [round(g, 3) for g in gaps])
print("not in candidates", sum("q_played" not in r for r in diff))
print(
    "differ pairs",
    Counter((r["played_type"], r["best_type"]) for r in diff).most_common(12),
)
print("played types all", Counter(r["played_type"] for r in rows).most_common())
qs = sorted(r["q_best"] for r in rows)
print("q_best quartiles", [qs[int(len(qs) * f)] for f in (0.1, 0.25, 0.5, 0.75, 0.9)])
# value trajectory per game: q_best at first and last position
for c in sorted(games):
    g = sorted((r for r in rows if r["capture"] == c), key=lambda r: r["turn"])
    print(
        g[0]["turn"],
        g[-1]["turn"],
        " ".join(f"{r['q_best']:+.2f}" for r in g[:: max(1, len(g) // 8)]),
    )
