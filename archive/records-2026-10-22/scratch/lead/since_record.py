"""Ranked record per account since a UTC time, with the mean opponent rating when known.

Usage: since_record.py 2026-10-13T02:13 [bot3,bot4,bot5,bot7]
"""

import json
import sys
from datetime import datetime, timezone

ROOT = "/home/owen/colonist-ai/data"
start = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=timezone.utc).timestamp()
accounts = (
    sys.argv[2].split(",") if len(sys.argv) > 2 else ["bot3", "bot4", "bot5", "bot7"]
)
keys = None
for acct in accounts:
    w = l = 0
    ratings = []
    with open(f"{ROOT}/{acct}/games.jsonl") as f:
        for line in f:
            try:
                g = json.loads(line)
            except ValueError:
                continue
            if g.get("queue") != "ranked" or (g.get("at") or 0) < start:
                continue
            if keys is None:
                keys = sorted(g)
            won = g.get("winner") == g.get("bot_color")
            w += won
            l += not won
            r = g.get("opponent_rating") or g.get("opp_rating")
            if isinstance(r, (int, float)):
                ratings.append(r)
    n = max(1, w + l)
    mean = f"{sum(ratings) / len(ratings):.0f}" if ratings else "-"
    print(
        f"{acct}: {w}-{l} ({w / n:.1%}, ±{196 * (0.25 / n) ** 0.5:.1f}), opp rating mean {mean} (n {len(ratings)})"
    )
print("keys:", keys)
