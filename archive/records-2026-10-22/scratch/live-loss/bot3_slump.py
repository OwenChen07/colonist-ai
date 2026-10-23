"""Dilly6283 (bot3) since Oct 21, 6 PM Toronto vs its own round 73 record before.

Per game: result, how it ended (quit share), the bot's seat, both MMRs at game
time (opp_rating.py's cache, filled here when missing). Seat from the converted
capture, cached in data/orchestrator/analysis/bot3_seats.json.

    .venv/bin/python data/orchestrator/scratch/live-loss/bot3_slump.py [days_before]
"""

import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.loss_report import wilson

TZ = ZoneInfo("America/Toronto")
CUT = datetime(
    2026, 10, 21, int(sys.argv[2]) if len(sys.argv) > 2 else 18, 0, tzinfo=TZ
).timestamp()
E80_END = datetime(2026, 10, 21, 20, 50, tzinfo=TZ).timestamp()
DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 4
MMR = Path("data/orchestrator/analysis/opponent_ratings.json")
SEATS = Path("data/orchestrator/analysis/bot3_seats.json")
BANDS = ((0, 2100), (2100, 2300), (2300, 9999))


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


games = []
for line in open("data/bot3/games.jsonl"):
    if not line.strip():
        continue
    g = json.loads(line)
    if g.get("queue") != "ranked" or g.get("own_bot") or g.get("winner") is None:
        continue
    if g["at"] < CUT - DAYS * 86400 or g.get("round") != 73:
        continue
    games.append(g)

mcache = json.loads(MMR.read_text()) if MMR.exists() else {}
scache = json.loads(SEATS.read_text()) if SEATS.exists() else {}
for g in games:
    cap = g["capture"]
    path = Path("captures/bot3") / cap
    if cap not in mcache:
        try:
            mcache[cap] = mmrs(path) if path.exists() else None
        except Exception as error:
            print(cap, error, file=sys.stderr)
            mcache[cap] = None
    if cap not in scache:
        try:
            rec = convert(load_capture(path)).record if path.exists() else None
            you = [c for c, p in rec["players"].items() if p["you"]] if rec else []
            scache[cap] = (
                (rec["seats"][0] == you[0])
                if len(you) == 1 and len(rec["seats"]) == 2
                else None
            )
        except Exception as error:
            print(cap, error, file=sys.stderr)
            scache[cap] = None
MMR.write_text(json.dumps(mcache, indent=0))
SEATS.write_text(json.dumps(scache))

rows = []
for g in games:
    m = mcache.get(g["capture"]) or {}
    rows.append(
        {
            "at": g["at"],
            "won": g["winner"] == g["bot_color"],
            "ended": g.get("ended"),
            "first": scache.get(g["capture"]),
            "opp": m.get("opp_mmr"),
            "bot": m.get("bot_mmr"),
            "chg": m.get("bot_change"),
        }
    )


def elo(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return 400 * math.log10(p / (1 - p))


def line(label, sel):
    n = len(sel)
    if not n:
        print(f"  {label:>16}: none")
        return
    w = sum(r["won"] for r in sel)
    rated = [r for r in sel if r["opp"] is not None and r["bot"] is not None]
    extra = ""
    if rated:
        exp = sum(1 / (1 + 10 ** ((r["opp"] - r["bot"]) / 400)) for r in rated) / len(
            rated
        )
        opp = sum(r["opp"] for r in rated) / len(rated)
        wr = sum(r["won"] for r in rated) / len(rated)
        extra = f" | opp mean {opp:.0f}, expected {100 * exp:.1f}%, actual-expected {100 * (wr - exp):+.1f}, perf {opp + elo(wr):.0f}"
    print(f"  {label:>16}: {w}-{n - w} {wilson(w, n)}{extra}")


def block(title, sel):
    print(title, f"({len(sel)} games)")
    line("all", sel)
    for lo, hi in BANDS:
        line(
            f"opp {lo}-{hi}" if hi < 9999 else f"opp {lo}+",
            [r for r in sel if r["opp"] is not None and lo <= r["opp"] < hi],
        )
    line("opp unknown", [r for r in sel if r["opp"] is None])
    line("bot first", [r for r in sel if r["first"] is True])
    line("bot second", [r for r in sel if r["first"] is False])
    ends = Counter((r["ended"], r["won"]) for r in sel)
    quits = sum(v for (e, _), v in ends.items() if e != "win")
    print(
        f"  ends: {dict(ends)}; not played out {quits}/{len(sel)} {wilson(quits, len(sel))}"
    )
    # quits by side: person quit = bot won by non-win; bot lost by non-win
    pq = sum(v for (e, won), v in ends.items() if e != "win" and won)
    bq = sum(v for (e, won), v in ends.items() if e != "win" and not won)
    print(f"  person quit {pq}, bot lost not played out {bq}")
    played = [r for r in sel if r["ended"] == "win"]
    line("played out only", played)
    chg = [r["chg"] for r in sel if r["chg"] is not None]
    if chg:
        print(f"  rating change sum {sum(chg):+.0f} over {len(chg)} games")


before = [r for r in rows if r["at"] < CUT]
after = [r for r in rows if r["at"] >= CUT]
print(
    f"bot3 round 73 ranked, {DAYS:g} days before cut; span",
    datetime.fromtimestamp(rows[0]["at"], TZ).strftime("%b %d %I:%M %p"),
    "..",
    datetime.fromtimestamp(rows[-1]["at"], TZ).strftime("%b %d %I:%M %p"),
)
block(f"BEFORE cut", before)
block(f"SINCE cut", after)
block("  of which E80 window (to 8:50 PM)", [r for r in after if r["at"] < E80_END])
block("  of which after E80 (8:50 PM on)", [r for r in after if r["at"] >= E80_END])


# rating-adjusted gap after vs before, with a normal-approx range
def resid(sel):
    rr = [r for r in sel if r["opp"] is not None and r["bot"] is not None]
    v = [r["won"] - 1 / (1 + 10 ** ((r["opp"] - r["bot"]) / 400)) for r in rr]
    m = sum(v) / len(v)
    var = sum((x - m) ** 2 for x in v) / (len(v) - 1)
    return m, var / len(v), len(v)


ma, va, na = resid(after)
mb, vb, nb = resid(before)
print(
    f"actual-expected after {100 * ma:+.1f} (n {na}) vs before {100 * mb:+.1f} (n {nb}); diff {100 * (ma - mb):+.1f} ± {196 * math.sqrt(va + vb):.1f}"
)
