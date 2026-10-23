"""Pull other bots' public games the way the site's own pages do.

1. The bot page's query: the last N completed games (Firestore, the site's
   public web config), saved to data/openhex/opponents/games.json.
2. GET /api/games/{id}/replay (public, no login) for up to PER_BOT games of each
   target bot, one request a second, saved to data/openhex/opponents/replays/.

Usage: pull_opponents.py [N games to list, default 200] [PER_BOT, default 30]
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

OUT = "/home/owen/colonist-ai/data/openhex/opponents"
APIKEY = "AIzaSyA2uNXw2pq-cjv5lKYEO305pQPqVyF8Oto"  # the site's public web config
FS = (
    "https://firestore.googleapis.com/v1/projects/open-hex/databases/(default)"
    f"/documents:runQuery?key={APIKEY}"
)
UA = {"User-Agent": "colonist-ai-r66", "Content-Type": "application/json"}
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
PER_BOT = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def val(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "nullValue" in v:
        return None
    if "mapValue" in v:
        return {k: val(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [val(x) for x in v["arrayValue"].get("values", [])]
    return str(v)


def list_games():
    q = {
        "structuredQuery": {
            "from": [{"collectionId": "games"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "status"},
                    "op": "IN",
                    "value": {
                        "arrayValue": {
                            "values": [
                                {"stringValue": "completed"},
                                {"stringValue": "aborted"},
                            ]
                        }
                    },
                }
            },
            "orderBy": [
                {"field": {"fieldPath": "completedAt"}, "direction": "DESCENDING"}
            ],
            "limit": N,
        }
    }
    req = urllib.request.Request(
        FS, data=json.dumps(q).encode(), headers=UA, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)
    games = []
    for row in rows:
        d = row.get("document")
        if not d:
            continue
        g = {k: val(v) for k, v in d.get("fields", {}).items()}
        g["id"] = d["name"].rsplit("/", 1)[1]
        games.append(g)
    return games


os.makedirs(f"{OUT}/replays", exist_ok=True)
games = list_games()
json.dump(games, open(f"{OUT}/games.json", "w"))
print(len(games), "games listed; keys:", sorted(games[0]) if games else None)
modes = {}
for g in games:
    modes[g.get("mode")] = modes.get(g.get("mode"), 0) + 1
print("modes", modes, "oldest", games[-1].get("completedAt") if games else None)
count = {}
for g in games:
    if g.get("mode") != "1v1" or g.get("status") != "completed":
        continue
    for p in g.get("players", []):
        count[p["name"]] = count.get(p["name"], 0) + 1
print("1v1 completed games per bot:", sorted(count.items(), key=lambda x: -x[1]))

board = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            "https://open-hex.web.app/api/leaderboard/1v1", headers=UA
        ),
        timeout=15,
    )
)
entries = board["entries"]
top_nn = [e["name"] for e in entries if e["name"].startswith("2s-NN")][:3]
targets = top_nn + [
    e["name"] for e in entries if e["name"] in ("catanrl-1v1", "rehex-2.11", "Asterism")
]
print("targets", targets)
json.dump(entries, open(f"{OUT}/leaderboard.json", "w"))

want = []
for name in targets:
    mine = [
        g["id"]
        for g in games
        if g.get("mode") == "1v1"
        and g.get("status") == "completed"
        and name in [p["name"] for p in g.get("players", [])]
    ][:PER_BOT]
    want += [i for i in mine if i not in want]
print(len(want), "replays wanted")
got = 0
for gid in want:
    path = f"{OUT}/replays/{gid}.json"
    if os.path.exists(path):
        continue
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                f"https://open-hex.web.app/api/games/{gid}/replay", headers=UA
            ),
            timeout=30,
        ) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        print(gid, e.code)
        continue
    open(path, "wb").write(data)
    got += 1
    time.sleep(1.0)
print("fetched", got)
