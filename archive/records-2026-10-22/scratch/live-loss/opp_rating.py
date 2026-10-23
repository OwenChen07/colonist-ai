"""Win rate by opponent rating, last N ranked games across bot3/4/5/7.

No API calls: colonist's game-over message (rankedUserStates) carries both
players' 1v1 MMR before and after the game, so the rating is the one at game
time. Cached per capture (MMRs only, no names) in
data/orchestrator/analysis/opponent_ratings.json.

    .venv/bin/python data/orchestrator/scratch/live-loss/opp_rating.py [N]
"""

import json
import math
import sys
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.loss_report import wilson

ACCOUNTS = (
    "bot3",
    "bot4",
    "bot5",
    "bot7",
    "bot8",
    "bot9",
)  # bot4/5/7 retired Oct 20, 9:47 AM
# bot8 (Vasos5306) counts only from Oct 21, 10:16:43 AM Toronto, when it left the people model for the best net
BOT8_CUT = 1792592203
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
CACHE = Path("data/orchestrator/analysis/opponent_ratings.json")
BANDS = ((0, 2100), (2100, 2300), (2300, 2500), (2500, 9999))

idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "ok":
            idx[r["capture"]] = r

games = []
for a in ACCOUNTS:
    for line in open(f"data/{a}/games.jsonl"):
        if not line.strip():
            continue
        g = json.loads(line)
        if (
            g.get("queue") != "ranked"
            or g.get("own_bot")
            or (a == "bot8" and g["at"] < BOT8_CUT)
        ):
            continue
        ix = idx.get(g["capture"])
        won = (
            ix["bot_won"]
            if ix and "bot_won" in ix
            else g.get("winner") == g.get("bot_color")
        )
        games.append((g["at"], a, g["capture"], bool(won), g.get("round")))
games.sort()
games = games[-N:]

cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def mmrs(path):
    you = None
    out = None
    for f in load_capture(path):
        d = f.data.get("data") if isinstance(f.data, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p = d["payload"]
        if you is None and "playerColor" in p:
            you = p["playerColor"]
        if "rankedUserStates" in p:
            out = p["rankedUserStates"]
    if you is None or not out:
        return None
    me = [u["rankedState"] for u in out if u.get("color") == you]
    them = [u["rankedState"] for u in out if u.get("color") != you]
    if len(me) != 1 or len(them) != 1:
        return None
    return {
        "bot_mmr": me[0].get("previousMMR"),
        "opp_mmr": them[0].get("previousMMR"),
        "bot_change": me[0].get("mostRecentChangeInValue"),
    }


missing = 0
for at, a, cap, won, rnd in games:
    if cap not in cache:
        path = Path("captures") / a / cap
        try:
            cache[cap] = mmrs(path) if path.exists() else None
        except Exception as error:
            print(f"{cap}: {error}", file=sys.stderr)
            cache[cap] = None
CACHE.write_text(json.dumps(cache, indent=0))

rows = []
for at, a, cap, won, rnd in games:
    m = cache.get(cap)
    if not m or m.get("opp_mmr") is None or m.get("bot_mmr") is None:
        missing += 1
        continue
    rows.append((a, won, m["opp_mmr"], m["bot_mmr"], m.get("bot_change"), rnd, at))


def elo(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return 400 * math.log10(p / (1 - p))


def expected(diffs):
    return sum(1 / (1 + 10 ** (d / 400)) for d in diffs) / len(diffs)


from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")
print(f"{len(games)} games, {len(rows)} with ratings, {missing} without")
print(
    "span",
    datetime.fromtimestamp(games[0][0], TZ).strftime("%b %d %I:%M %p"),
    "..",
    datetime.fromtimestamp(games[-1][0], TZ).strftime("%b %d %I:%M %p"),
)


def report(label, sel):
    if not sel:
        print(f"{label}: none")
        return
    w = sum(1 for r in sel if r[1])
    n = len(sel)
    p = w / n
    opp = sum(r[2] for r in sel) / n
    bot = sum(r[3] for r in sel) / n
    exp = expected([r[2] - r[3] for r in sel])
    print(
        f"{label:>12}: {wilson(w, n)}  Elo vs opp {elo(p):+.0f}  "
        f"opp mean {opp:.0f}  bot mean {bot:.0f}  perf {opp + elo(p):.0f}  "
        f"expected from ratings {100 * exp:.1f}%"
    )


print("by opponent rating at game time")
for lo, hi in BANDS:
    report(
        f"{lo}-{hi}" if hi < 9999 else f"{lo}+", [r for r in rows if lo <= r[2] < hi]
    )
report("all", rows)
print("by account")
for a in ACCOUNTS:
    report(a, [r for r in rows if r[0] == a])
print("by opponent minus bot rating")
for lo, hi in ((-9999, -200), (-200, -100), (-100, 0), (0, 100), (100, 9999)):
    report(f"{lo}..{hi}", [r for r in rows if lo <= r[2] - r[3] < hi])
print("points per win / loss and break-even win rate, by opponent minus bot rating")
for lo, hi in ((-9999, -300), (-300, -200), (-200, -100), (-100, 0), (0, 9999)):
    sel = [r for r in rows if lo <= r[2] - r[3] < hi and r[4] is not None]
    w = [r[4] for r in sel if r[1]]
    l = [r[4] for r in sel if not r[1]]
    if w and l:
        mw, ml = sum(w) / len(w), sum(l) / len(l)
        print(
            f"  {lo}..{hi}: win {mw:+.1f} (n {len(w)}) loss {ml:+.1f} (n {len(l)}) "
            f"break-even {100 * -ml / (mw - ml):.1f}%  actual {wilson(len(w), len(sel))}"
        )
print("rating points per game: win", end=" ")
wins = [r[4] for r in rows if r[1] and r[4] is not None]
losses = [r[4] for r in rows if not r[1] and r[4] is not None]
print(
    f"{sum(wins) / max(1, len(wins)):+.1f} (n {len(wins)}), loss {sum(losses) / max(1, len(losses)):+.1f} (n {len(losses)})"
)
