"""E61 interim: ranked record per account since the start, and how games ended."""

import json
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = "/home/owen/colonist-ai/data"
START = (
    datetime.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-10-19T19:29:27")
    .replace(tzinfo=timezone.utc)
    .timestamp()
)
ACCOUNTS = (
    sys.argv[1].split(",") if len(sys.argv) > 1 else ["bot4", "bot3", "bot5", "bot7"]
)

pooled = [0, 0]
by_round = Counter()
for acct in ACCOUNTS:
    w = l = 0
    ends = Counter()
    last = 0
    with open(f"{ROOT}/{acct}/games.jsonl") as f:
        for line in f:
            try:
                g = json.loads(line)
            except ValueError:
                continue
            if g.get("queue") != "ranked" or (g.get("at") or 0) < START:
                continue
            won = g.get("winner") == g.get("bot_color")
            w += won
            l += not won
            ends[(("W" if won else "L"), g.get("ended"))] += 1
            arm = acct if acct == ACCOUNTS[0] else "controls"
            by_round[(arm, g.get("round"), "W" if won else "L")] += 1
            last = max(last, g["at"])
    if acct != ACCOUNTS[0]:
        pooled[0] += w
        pooled[1] += l
    n = max(1, w + l)
    lt = (
        datetime.fromtimestamp(last, timezone.utc).strftime("%H:%M UTC")
        if last
        else "-"
    )
    print(f"{acct}: {w}-{l} ({w / n:.1%}), last {lt}, ends {dict(ends)}")
n = max(1, sum(pooled))
print(f"controls pooled: {pooled[0]}-{pooled[1]} ({pooled[0] / n:.1%})")
for arm, rnd in sorted({(a, r) for a, r, _ in by_round}, key=str):
    print(f"{arm} round {rnd}: {by_round[(arm, rnd, 'W')]}-{by_round[(arm, rnd, 'L')]}")
