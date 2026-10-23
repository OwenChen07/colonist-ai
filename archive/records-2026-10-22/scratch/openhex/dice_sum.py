"""Summarise dice_match.jsonl by sims: shadow (training-like dice input) score."""

import json
import math
from collections import defaultdict

rows = defaultdict(list)
with open("data/orchestrator/scratch/openhex/dice_match.jsonl") as f:
    for line in f:
        r = json.loads(line)
        rows[r["sims"]].append(r)
for sims, rs in sorted(rows.items()):
    n = len(rs)
    s = [r["shadow_score"] for r in rs]
    p = sum(s) / n
    se = math.sqrt(max(p * (1 - p), 1e-9) / n)
    first = [r["shadow_score"] for r in rs if r["shadow_first"]]
    broke = sum(r["broke"] for r in rs) / n
    print(
        f"sims {sims}: {n} games, shadow scores {p:.1%} +- {1.96 * se:.1%} (95%); "
        f"shadow seated first {sum(first) / len(first):.1%}; live model broke in {broke:.0%}; "
        f"mean turns {sum(r['turns'] for r in rs) / n:.0f}"
    )
