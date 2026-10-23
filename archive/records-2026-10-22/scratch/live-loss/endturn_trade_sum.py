"""Summary of endturn_trade_positions.jsonl."""

import json
import math
import statistics

rows = [
    json.loads(x)
    for x in open("data/orchestrator/live-loss/endturn_trade_positions.jsonl")
]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 2), round(c + h, 2))


for group in ("disagree", "control"):
    g = [r for r in rows if r["group"] == group and r["n_trades"] > 0]
    print(
        f"== {group}: {len(g)} positions with a legal trade "
        f"({sum(r['group'] == group for r in rows)} total)"
    )
    for i, label in enumerate(("3200 s1", "3200 s2", "3200 s3", "6400")):
        k = sum(r["runs"][i]["best_type"] == "MARITIME_TRADE" for r in g)
        gaps = [r["runs"][i]["q_best_trade"] - r["runs"][i]["q_end"] for r in g]
        print(
            f"  {label}: trade best {k}/{len(g)} {wilson(k, len(g))}  "
            f"median gap {statistics.median(gaps):+.3f}"
            if g
            else ""
        )
    stable = [
        r for r in g if sum(x["best_type"] == "MARITIME_TRADE" for x in r["runs"]) >= 3
    ]
    print(f"  trade best in >=3 of 4 runs: {len(stable)}/{len(g)}")
    for r in stable:
        gs = [x["q_best_trade"] - x["q_end"] for x in r["runs"]]
        print(
            "   ",
            r["turn"],
            r["n_trades"],
            " ".join(f"{x:+.2f}" for x in gs),
            f"q_end6400 {r['runs'][3]['q_end']:+.2f}",
        )
