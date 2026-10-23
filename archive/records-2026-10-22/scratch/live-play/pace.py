"""Games per day and minutes between games per account since a UTC time.
Usage: pace.py SINCE_UTC(e.g. 2026-10-19T19:29:27) [accts]"""

import datetime as dt
import json
import statistics
import sys
import time

ROOT = "/home/owen/colonist-ai/data"
since = (
    dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=dt.timezone.utc).timestamp()
)
accts = (
    sys.argv[2].split(",")
    if len(sys.argv) > 2
    else ["bot4", "bot3", "bot5", "bot7", "bot8"]
)
hours = (time.time() - since) / 3600
print(f"window {hours:.1f} h")
for acct in accts:
    ats = []
    try:
        lines = open(f"{ROOT}/{acct}/games.jsonl").read().splitlines()
    except OSError:
        continue
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("at", 0) >= since and r.get("queue", "ranked") == "ranked":
            ats.append(r["at"])
    ats.sort()
    gaps = [(b - a) / 60 for a, b in zip(ats, ats[1:])]
    med = statistics.median(gaps) if gaps else 0
    print(
        f"{acct}: ranked {len(ats):3}  per day {len(ats) / hours * 24:5.1f}  "
        f"median minutes between game ends {med:5.1f}"
    )
