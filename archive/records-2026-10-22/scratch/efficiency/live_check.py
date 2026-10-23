"""Print each bot card on the spectator site: name, status, source, updatedAt."""

import datetime
import json
import sys
import urllib.request
from zoneinfo import ZoneInfo

URL = "https://colonist-ai-spectator.vercel.app/api/live"
TZ = ZoneInfo("America/Toronto")

with urllib.request.urlopen(URL, timeout=30) as r:
    data = json.load(r)
print("now", datetime.datetime.now(TZ).strftime("%I:%M:%S %p"))
print("site updatedAt", data.get("updatedAt"))
print("cards", len(data["bots"]))
keys = set()
for b in data["bots"]:
    keys |= set(b)
    rating = (b.get("rank") or {}).get("rating")
    print(
        f"{b.get('name'):12} {b.get('status'):10} rating={rating} "
        f"source={b.get('source')} updatedAt={b.get('updatedAt')} account={b.get('account')}"
    )
print("bot keys:", sorted(keys))
if "--page" in sys.argv:
    page = urllib.request.urlopen(URL.replace("/api/live", "/"), timeout=30).read()
    print([l.strip() for l in page.decode().splitlines() if "<p" in l][:2])
if "--raw" in sys.argv:
    print(json.dumps(data["bots"], indent=1)[:4000])
