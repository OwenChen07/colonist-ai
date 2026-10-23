"""Results of our OpenHex games (from data/openhex/games) and our leaderboard row."""

import glob
import json
import math
import os
import urllib.request

GAMES = "/home/owen/colonist-ai/data/openhex/games"
rows = []
for path in sorted(glob.glob(f"{GAMES}/*.json"), key=os.path.getmtime):
    rec = json.load(open(path))
    g = rec.get("game", {})
    if g.get("status") == "active":
        print(rec["id"][:8], "still active")
        continue
    w = g.get("winner")
    w = w.get("color") if isinstance(w, dict) else w
    opp = [p["name"] for p in g.get("players", []) if p.get("color") != rec.get("me")]
    notes = [n[1] for n in rec.get("notes", []) if isinstance(n[1], str)]
    sync = any("OUT OF SYNC" in n for n in notes)
    fallbacks = sum(1 for m in rec.get("moves", []) if m[0] == "fallback")
    rows.append(
        (rec["id"][:8], g.get("status"), w == rec.get("me"), opp, sync, fallbacks)
    )
    print(rows[-1])
clean = [r for r in rows if not r[4]]
for name, rs in (("all", rows), ("never out of sync", clean)):
    n, k = len(rs), sum(r[2] for r in rs)
    if n:
        p = k / n
        ci = 1.96 * math.sqrt(p * (1 - p) / n)
        print(f"{name}: {k}/{n} wins = {p:.0%} +- {ci:.0%}")
req = urllib.request.Request(
    "https://open-hex.web.app/api/leaderboard/1v1",
    headers={"User-Agent": "colonist-ai-r66"},
)
board = json.load(urllib.request.urlopen(req, timeout=15))
for e in board["entries"]:
    if e["name"] == "colonist-ai-r66" or e["rank"] <= 3:
        print(e)
    elif e["name"] in ("hexn-4d.0", "rehex-2.11", "catanrl-1v1") or e[
        "name"
    ].startswith("ar-"):
        print(f"  opp #{e['rank']} {e['name']} elo {e['elo']:.0f} games {e['games']}")
print(len(board["entries"]), "bots")
