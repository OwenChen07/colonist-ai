"""E78 live read: bot8 (guard) vs bot12 (control) since START epoch.

e78_live.py START
"""

import json
import sys
from collections import Counter

START = float(sys.argv[1])
for acc in ("bot8", "bot12"):
    w = n = 0
    ended = Counter()
    rat = []
    for line in open(f"data/{acc}/games.jsonl"):
        g = json.loads(line)
        if g.get("at", 0) < START or g.get("bot_color") is None:
            continue
        n += 1
        won = g.get("winner") == g.get("bot_color")
        w += won
        ended[(g.get("ended"), won)] += 1
        for k in ("opp_rating", "opponent_rating"):
            if g.get(k):
                rat.append(g[k])
    print(
        acc,
        f"{w}-{n - w}",
        dict(ended),
        "opp mean",
        round(sum(rat) / len(rat)) if rat else None,
    )
