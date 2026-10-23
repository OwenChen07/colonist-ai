"""Standing metric (lead, Oct 19): once a day before the 9 AM lead run, append a
dated row to data/orchestrator/analysis/opponent_ratings_daily.md with the last
400 and last 1,200 ranked games by opponent-rating band and the performance
rating against 2300+. Reads the MMR cache opp_rating.py fills; run
opp_rating.py 1200 first so new captures are cached.

    .venv/bin/python data/orchestrator/scratch/live-loss/opp_daily.py [--dry]
"""

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
CACHE = json.loads(Path("data/orchestrator/analysis/opponent_ratings.json").read_text())
OUT = Path("data/orchestrator/analysis/opponent_ratings_daily.md")
TZ = ZoneInfo("America/Toronto")
BANDS = (("<2100", 0, 2100), ("2100-2300", 2100, 2300), ("2300+", 2300, 99999))

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
        games.append((g["at"], g["capture"], bool(won)))
games.sort()


def elo(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return 400 * math.log10(p / (1 - p))


def wilson(w, n, z=1.96):
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def cells(n):
    sel = games[-n:]
    rows = []
    for at, cap, won in sel:
        m = CACHE.get(cap)
        if m and m.get("opp_mmr") is not None:
            rows.append((won, m["opp_mmr"]))
    out = []
    perf = "n/a"
    for name, lo, hi in BANDS:
        b = [r for r in rows if lo <= r[1] < hi]
        if not b:
            out.append("none")
            continue
        w = sum(r[0] for r in b)
        lo_ci, hi_ci = wilson(w, len(b))
        out.append(f"{100 * w / len(b):.0f}% ({w}/{len(b)}) [{lo_ci:.0f}-{hi_ci:.0f}]")
        if name == "2300+":
            opp = sum(r[1] for r in b) / len(b)
            perf = f"{opp + elo(w / len(b)):.0f}"
    w = sum(r[0] for r in rows)
    out.append(f"{100 * w / len(rows):.1f}% (n {len(rows)})")
    span = f"{datetime.fromtimestamp(sel[0][0], TZ):%b %d %-I:%M %p} to {datetime.fromtimestamp(sel[-1][0], TZ):%b %d %-I:%M %p}"
    return out, perf, span


now = datetime.now(TZ)
c4, p4, s4 = cells(400)
c12, p12, s12 = cells(1200)
row = (
    f"| {now:%b %d, %-I:%M %p} | "
    + " | ".join(c4)
    + f" | {p4} | "
    + " | ".join(c12)
    + f" | {p12} | {s4} | {s12} |"
)
header = (
    "# Win rate by opponent rating, daily\n\n"
    "One row a day, written before the 9 AM lead run. Ranked games on bot3, bot4, bot5, bot7, (from Oct 20) bot9 and (from Oct 21, 10:16 AM, best net) bot8. "
    "Ratings are the players' 1v1 ratings at game time. Brackets are 95% intervals. "
    "Perf is the performance rating against 2300+ opponents (their mean rating plus the Elo gap of the win rate); "
    "rank 1 needs about 2650.\n\n"
    "| Written | 400: <2100 | 400: 2100-2300 | 400: 2300+ | 400: all | 400: perf 2300+ "
    "| 1200: <2100 | 1200: 2100-2300 | 1200: 2300+ | 1200: all | 1200: perf 2300+ | 400 span | 1200 span |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)
print(row)
if "--dry" not in sys.argv:
    text = OUT.read_text() if OUT.exists() else header
    OUT.write_text(text + row + "\n")
    print("appended to", OUT)
