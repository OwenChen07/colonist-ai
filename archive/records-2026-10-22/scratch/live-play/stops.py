"""Bridge stops (games the bot left mid-game) since a capture time, per account.
These games are missing from games.jsonl, so a record from it skips them."""

import re
import sys

SINCE = sys.argv[1]  # e.g. 20261019-192927 (UTC, as in capture names)
ACCOUNTS = (
    sys.argv[2].split(",") if len(sys.argv) > 2 else ["bot4", "bot3", "bot5", "bot7"]
)
SAVED = re.compile(r"bridge-(\d{8}-\d{6})\.json")
for acct in ACCOUNTS:
    last, stops = "", []
    with open(f"/home/owen/colonist-ai/data/{acct}/bridge.log", errors="replace") as f:
        for line in f:
            m = SAVED.search(line)
            if m:
                last = m.group(1)
            elif line.startswith("  stopped:") and last >= SINCE:
                stops.append(f"{last} {line.strip()[9:90]}")
    print(f"{acct}: {len(stops)} stops")
    for s in stops:
        print("   ", s)
