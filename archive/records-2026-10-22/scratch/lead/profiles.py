"""Print each account's 1v1 ranked standing from colonist's public API."""

import json
import sys
import urllib.request

API = "https://colonist.io/api"
HEADERS = {"User-Agent": "colonist-ai-status/1.0", "Accept": "application/json"}


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.read()


seasons = json.loads(fetch(f"{API}/profile/ranked-seasons")[1])
season = max(s["seasonType"] for s in seasons if isinstance(s.get("seasonType"), int))
for name in sys.argv[1:]:
    try:
        status, body = fetch(f"{API}/profile/{name}/ranked/{season}")
        modes = json.loads(body)
        duel = next((m for m in modes if m.get("modeType") == 1), {})
        keys = (
            "skillRating",
            "gamesPlayed",
            "winRate",
            "rankedStanding",
            "currentRankedState",
        )
        text = {k: duel.get(k) for k in keys}
    except Exception as error:
        status, text = "ERR", str(error)[:300]
    print(f"{name}: {status} {text}")
