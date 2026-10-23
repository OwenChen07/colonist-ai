"""List one account's ranked games since a UTC time: Toronto time, result, end, rolls,
seat, ratings, net label, simulations. No names.

    .venv/bin/python data/orchestrator/scratch/live-play/acct_games.py bot5 2026-10-20T00:50:00
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

acct = sys.argv[1]
start = datetime.fromisoformat(sys.argv[2]).replace(tzinfo=timezone.utc).timestamp()
TZ = ZoneInfo("America/Toronto")
idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        idx[r["capture"]] = r
cache = json.load(open("data/orchestrator/analysis/opponent_ratings.json"))
by = Counter()
for line in open(f"data/{acct}/games.jsonl"):
    if not line.strip():
        continue
    g = json.loads(line)
    if g.get("queue") != "ranked" or (g.get("at") or 0) < start:
        continue
    won = g.get("winner") == g.get("bot_color")
    ix = idx.get(g["capture"], {})
    m = cache.get(g["capture"]) or {}
    first = ix.get("seat") if ix else None
    t = datetime.fromtimestamp(g["at"], TZ).strftime("%I:%M %p")
    print(
        f"{t} {'W' if won else 'L'} {g.get('ended'):<11} rolls {ix.get('rolls')} opp {m.get('opp_mmr')} "
        f"bot {m.get('bot_mmr')} {g.get('label')} sims {g.get('simulations')} own {g.get('own_bot')} "
        f"bot_cities {ix.get('bot', {}).get('cities')} opp_cities {ix.get('human', {}).get('cities')}"
    )
    by[(g.get("label"), won)] += 1
print(dict(by))
