"""One account's ranked record in a time segment, with and without own-bot games, and the
last known rating. UTC start, optional UTC end.

    .venv/bin/python data/orchestrator/scratch/live-play/seg_tally.py bot8 2026-10-19T00:00:00 [END]
"""

import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

acct = sys.argv[1]
ts = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
start = ts(sys.argv[2])
end = ts(sys.argv[3]) if len(sys.argv) > 3 else 1e12
TZ = ZoneInfo("America/Toronto")
cache = json.load(open("data/orchestrator/analysis/opponent_ratings.json"))
w = l = ow = ol = 0
last = None
first_at = last_at = None
for line in open(f"data/{acct}/games.jsonl"):
    if not line.strip():
        continue
    g = json.loads(line)
    at = g.get("at") or 0
    if g.get("queue") != "ranked" or not (start <= at < end):
        continue
    won = g.get("winner") == g.get("bot_color")
    if g.get("own_bot"):
        ow += won
        ol += not won
    else:
        w += won
        l += not won
    first_at = first_at or at
    last_at = at
    m = cache.get(g["capture"]) or {}
    if m.get("bot_mmr"):
        last = (m["bot_mmr"], at)
f = lambda t: datetime.fromtimestamp(t, TZ).strftime("%b %d, %I:%M %p") if t else None
print(
    f"{acct} ranked {w}-{l} ({w / max(1, w + l):.1%}) vs people; own-bot {ow}-{ol}; "
    f"first {f(first_at)} last {f(last_at)}"
)
print(
    "last known rating", last[0] if last else None, "at", f(last[1]) if last else None
)
