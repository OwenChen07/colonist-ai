"""The public bot list, as the site's home and bot pages read it (one Firestore
list of "bots", one of "queue"), plus the 1v1 leaderboard. Three requests.
Saves data/openhex/opponents/bots.json and leaderboard.json; prints one line per
1v1 bot: rank, Elo, games, wins, created, last active, description.
"""

import json
import urllib.request


def val(v):
    if "mapValue" in v:
        return {k: val(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [val(x) for x in v["arrayValue"].get("values", [])]
    if "integerValue" in v:
        return int(v["integerValue"])
    for k in ("stringValue", "doubleValue", "booleanValue", "timestampValue"):
        if k in v:
            return v[k]
    return None


OUT = "/home/owen/colonist-ai/data/openhex/opponents"
APIKEY = "AIzaSyA2uNXw2pq-cjv5lKYEO305pQPqVyF8Oto"  # the site's public web config
BASE = "https://firestore.googleapis.com/v1/projects/open-hex/databases/(default)/documents"
UA = {"User-Agent": "colonist-ai-r66"}


def get(url):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30
    ) as r:
        return json.load(r)


def docs(coll):
    out = []
    d = get(f"{BASE}/{coll}?key={APIKEY}&pageSize=300")
    for doc in d.get("documents", []):
        x = {k: val(v) for k, v in doc.get("fields", {}).items()}
        x["id"] = doc["name"].rsplit("/", 1)[1]
        x["createTime"] = doc.get("createTime")
        x["updateTime"] = doc.get("updateTime")
        out.append(x)
    return out


bots = docs("bots")
json.dump(bots, open(f"{OUT}/bots.json", "w"), indent=1)
queue = docs("queue")
board = get("https://open-hex.web.app/api/leaderboard/1v1")["entries"]
json.dump(board, open(f"{OUT}/leaderboard.json", "w"))
print("bot fields:", sorted({k for b in bots for k in b}))
print(
    "queue now:",
    [(q.get("name") or q.get("botName") or q["id"], q.get("mode")) for q in queue],
)
byid = {b["id"]: b for b in bots}
for e in board:
    b = byid.get(e["bot_id"], {})
    print(
        f'{e["rank"]:>2} {e["name"]:<24} elo {e["elo"]:>6.0f} games {e["games"]:>5} wins {e["wins"]:>5} '
        f'created {str(b.get("createdAt") or b.get("createTime"))[:10]} updated {str(b.get("updateTime"))[:16]} '
        f'last {str(b.get("lastGameAt") or b.get("lastActive") or "")[:16]} | {b.get("description", "")}'
    )
