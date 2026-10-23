"""Own-bot meetings: per day and per pair, from data/*/games.jsonl.
usage: own_meet.py [SINCE_UTC_DATE]"""

import collections
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

TOR = ZoneInfo("America/Toronto")
since = sys.argv[1] if len(sys.argv) > 1 else "2026-10-12"
cut = dt.datetime.fromisoformat(since).replace(tzinfo=TOR).timestamp()
days = collections.Counter()
pairs = collections.Counter()
total = collections.Counter()
starts = []
for path in sorted(Path("data").glob("*/games.jsonl")):
    acct = path.parent.name
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("at", 0) < cut or r.get("queue", "ranked") != "ranked":
            continue
        day = dt.datetime.fromtimestamp(r["at"], TOR).date()
        total[day] += 1
        if r.get("own_bot"):
            days[day] += 1
            pairs[acct] += 1
print("day  ranked-rows  own-bot-rows (each meeting is 2 rows if both accounts log it)")
for d in sorted(total):
    print(d, total[d], days[d])
print("own-bot rows per account:", dict(pairs))
keys = set()
for path in sorted(Path("data").glob("bot3/games.jsonl")):
    for line in path.read_text().splitlines()[-3:]:
        keys |= set(json.loads(line))
print(sorted(keys))
