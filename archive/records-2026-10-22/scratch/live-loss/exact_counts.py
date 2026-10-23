"""Counts of the opponent_exact export from data/human/index.jsonl.

.venv/bin/python data/orchestrator/scratch/live-loss/exact_counts.py
"""

import json
from collections import Counter

rows = [json.loads(l) for l in open("/home/owen/colonist-ai/data/human/index.jsonl")]
ok = [r for r in rows if r.get("status") == "ok"]
for split in ("train", "holdout"):
    for won in (None, False):
        sel = [
            r
            for r in ok
            if r["split"] == split and (won is None or r["bot_won"] is won)
        ]
        hands, known, exact, skipped = Counter(), 0, 0, Counter()
        for r in sel:
            hands.update(r.get("hands", {}))
            e = r["exported"]
            known += e["opponent"] if isinstance(e.get("opponent"), int) else 0
            x = e.get("opponent_exact")
            if isinstance(x, int):
                exact += x
            else:
                skipped[str(x)[:40]] += 1
        n = sum(hands.values())
        share = {
            k: f"{v} ({100 * v / max(n, 1):.1f}%)" for k, v in sorted(hands.items())
        }
        tag = "person won" if won is False else "all"
        print(
            f"{split} {tag}: games {len(sel)}, known {known}, exact {exact} "
            f"(+{100 * (exact - known) / max(known, 1):.0f}%), skipped {dict(skipped)}"
        )
        print(f"   {share}")
print("missing hands:", sum(1 for r in ok if "hands" not in r))
