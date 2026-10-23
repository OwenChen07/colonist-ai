"""E61: per account since the start, win rate against what the ratings expected,
performance rating, and rating points gained per day. Reads live-loss's MMR cache
(opponent_ratings.json) and fills gaps from captures without writing it back.

    .venv/bin/python data/orchestrator/scratch/live-play/e61_band.py [start_utc_iso] [arm,control,...]
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402

START = (
    datetime.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-10-19T19:29:27")
    .replace(tzinfo=timezone.utc)
    .timestamp()
)
ACCOUNTS = (
    sys.argv[2].split(",") if len(sys.argv) > 2 else ["bot4", "bot3", "bot5", "bot7"]
)  # arm first
CACHE = Path("data/orchestrator/analysis/opponent_ratings.json")
cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def mmrs(path):
    you = out = None
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


def elo(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return 400 * math.log10(p / (1 - p))


now = datetime.now(timezone.utc).timestamp()
days = (now - START) / 86400
pool = []
for a in ACCOUNTS:
    rows = []
    miss = 0
    for line in open(f"data/{a}/games.jsonl"):
        if not line.strip():
            continue
        g = json.loads(line)
        if g.get("queue") != "ranked" or g.get("own_bot") or (g.get("at") or 0) < START:
            continue
        won = g.get("winner") == g.get("bot_color")
        m = cache.get(g["capture"])
        if m is None and g["capture"] not in cache:
            p = Path("captures") / a / g["capture"]
            try:
                m = mmrs(p) if p.exists() else None
            except Exception:
                m = None
        if not m or m.get("opp_mmr") is None or m.get("bot_mmr") is None:
            miss += 1
            continue
        rows.append((won, m["opp_mmr"], m["bot_mmr"], m.get("bot_change")))
    if a != ACCOUNTS[0]:
        pool += rows
    for label, sel in ((a, rows),) + (
        (("controls", pool),) if a == ACCOUNTS[-1] else ()
    ):
        n = len(sel)
        if not n:
            print(f"{label}: none")
            continue
        w = sum(r[0] for r in sel)
        exp = sum(1 / (1 + 10 ** ((r[1] - r[2]) / 400)) for r in sel) / n
        opp = sum(r[1] for r in sel) / n
        pts = sum(r[3] or 0 for r in sel)
        hi = [r for r in sel if r[1] >= 2300]
        print(
            f"{label:>8}: {w}-{n - w} ({w / n:.1%}) expected {exp:.1%}  opp mean {opp:.0f}  "
            f"perf {opp + elo(w / n):.0f}  2300+: {sum(r[0] for r in hi)}-{len(hi) - sum(r[0] for r in hi)}  "
            f"points {pts:+.0f} ({pts / days:+.0f}/day)"
            + (f"  no rating {miss}" if label == a else "")
        )
print(f"window {days:.2f} days")
