"""Shared loader for live comparisons: ranked games of bot3/4/5/7 with opponent
hash and MMRs (cached in data/orchestrator/analysis/opponent_hash.json, same cache
as bot5_gap.py), meeting number and dice rolls. No names leave this module."""

import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from catanatron.colonist.capture import load as load_capture

ACCOUNTS = (
    "bot3",
    "bot4",
    "bot5",
    "bot7",
    "bot8",
    "bot9",
    "bot10",
    "bot11",
    "bot12",
)  # bot4/5/7 retired Oct 20, 9:47 AM
# bot8 (Vasos5306) ran the people model (E57) until Oct 21, 10:16:43 AM Toronto and the
# best net after; only games from then on count, never pooled with the people-model games.
BOT8_CUT = 1792592203


def counted(account, g):
    return account != "bot8" or g.get("at", 0) >= BOT8_CUT


TZ = ZoneInfo("America/Toronto")
BANDS = ((0, 2100), (2100, 2300), (2300, 9999))
RCACHE = Path("data/orchestrator/analysis/opponent_ratings.json")
OCACHE = Path("data/orchestrator/analysis/opponent_hash.json")


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.0f}, {100 * (c + h):.0f}] ({k}-{n - k})"


def fmt_t(t):
    return datetime.fromtimestamp(t, TZ).strftime("%b %d %I:%M %p")


def scan(path):
    you = None
    names = {}
    ranked = None
    for f in load_capture(path):
        d = f.data.get("data") if isinstance(f.data, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p = d["payload"]
        if you is None and "playerColor" in p:
            you = p["playerColor"]
        if "rankedUserStates" in p:
            ranked = p["rankedUserStates"]
        gs = p.get("playerUserStates")
        if isinstance(gs, list):
            for u in gs:
                if isinstance(u, dict) and "selectedColor" in u and u.get("username"):
                    names[u["selectedColor"]] = u["username"]
    out = {"opp": None, "bot_mmr": None, "opp_mmr": None}
    if you is None:
        return out
    them = [n for c, n in names.items() if c != you]
    if len(them) == 1:
        out["opp"] = hashlib.sha256(them[0].encode()).hexdigest()[:8]
    if ranked:
        me = [u["rankedState"] for u in ranked if u.get("color") == you]
        ot = [u["rankedState"] for u in ranked if u.get("color") != you]
        if len(me) == 1 and len(ot) == 1:
            out["bot_mmr"] = me[0].get("previousMMR")
            out["opp_mmr"] = ot[0].get("previousMMR")
    return out


def load_rows():
    idx = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok":
                idx[r["capture"]] = r
    rc = json.loads(RCACHE.read_text()) if RCACHE.exists() else {}
    oc = json.loads(OCACHE.read_text()) if OCACHE.exists() else {}
    games = []
    for a in ACCOUNTS:
        if not Path(f"data/{a}/games.jsonl").exists():
            continue
        for line in open(f"data/{a}/games.jsonl"):
            if not line.strip():
                continue
            g = json.loads(line)
            if (
                g.get("queue") != "ranked"
                or g.get("own_bot")
                or g.get("winner") is None
                or not counted(a, g)
            ):
                continue
            g["account"] = a
            games.append(g)
    games.sort(key=lambda g: g["at"])
    todo = [g for g in games if g["capture"] not in oc]
    print(f"{len(games)} ranked games, scanning {len(todo)} captures", file=sys.stderr)
    for i, g in enumerate(todo):
        path = Path("captures") / g["account"] / g["capture"]
        try:
            oc[g["capture"]] = scan(path) if path.exists() else None
        except Exception as error:
            print(f"{g['capture']}: {error}", file=sys.stderr)
            oc[g["capture"]] = None
        if i % 200 == 199:
            OCACHE.write_text(json.dumps(oc, indent=0))
    OCACHE.write_text(json.dumps(oc, indent=0))
    seen = defaultdict(int)
    rows = []
    for g in games:
        s = oc.get(g["capture"]) or {}
        r = rc.get(g["capture"]) or {}
        opp = s.get("opp")
        nth = None
        if opp:
            seen[(g["account"], opp)] += 1
            nth = seen[(g["account"], opp)]
        ix = idx.get(g["capture"])
        rows.append(
            {
                "a": g["account"],
                "at": g["at"],
                "won": g["winner"] == g["bot_color"],
                "opp": opp,
                "nth": nth,
                "opp_mmr": s.get("opp_mmr") or r.get("opp_mmr"),
                "bot_mmr": s.get("bot_mmr") or r.get("bot_mmr"),
                "rolls": ix.get("rolls") if ix else None,
                "round": g.get("round"),
                "label": g.get("label") or "",
                "ended": g.get("ended"),
            }
        )
    return rows


def band_rates(sel, bands=BANDS):
    out = {}
    for lo, hi in bands:
        b = [r for r in sel if r["opp_mmr"] is not None and lo <= r["opp_mmr"] < hi]
        out[(lo, hi)] = (sum(r["won"] for r in b), len(b))
    return out


def standardized(x, y):
    """x minus y, with y's band win rates weighted by x's band mix; bootstrap 95%."""

    def diff(x, y):
        n = sum(v[1] for k, v in x.items() if y[k][1])
        if not n:
            return float("nan")
        return (
            sum(
                nx * (w / nx - y[k][0] / y[k][1])
                for k, (w, nx) in x.items()
                if nx and y[k][1]
            )
            / n
        )

    d = diff(x, y)
    rng = random.Random(1)
    boots = []
    for _ in range(2000):
        bx = {
            k: (sum(rng.random() < w / n for _ in range(n)), n) if n else (0, 0)
            for k, (w, n) in x.items()
        }
        by = {
            k: (sum(rng.random() < w / n for _ in range(n)), n) if n else (0, 0)
            for k, (w, n) in y.items()
        }
        boots.append(diff(bx, by))
    boots.sort()
    return d, boots[50], boots[1949]


FINE = [(x, x + 100) for x in range(1500, 2800, 100)]


def summary(name, sel):
    rated = [r for r in sel if r["opp_mmr"] is not None]
    mo = sum(r["opp_mmr"] for r in rated) / max(1, len(rated))
    br = band_rates(sel)
    rolled = [r for r in sel if r["rolls"]]
    long = [r for r in rolled if r["rolls"] >= 90]
    print(
        f"{name:>14}: {wilson(sum(r['won'] for r in sel), len(sel))}  rated {len(rated)} opp mean {mo:.0f}"
    )
    print(
        "                bands: "
        + "; ".join(
            f"{lo}-{hi if hi < 9999 else ''}: {wilson(*br[(lo, hi)])}"
            for lo, hi in BANDS
        )
    )
    print(
        f"                90+ rolls {len(long)}/{len(rolled)} replayed: {wilson(sum(r['won'] for r in long), len(long))}"
    )


def compare(label, x, y):
    if not x or not y:
        print(f"  {label}: not enough games")
        return
    raw = sum(r["won"] for r in x) / len(x) - sum(r["won"] for r in y) / len(y)
    d, lo, hi = standardized(band_rates(x, FINE), band_rates(y, FINE))
    print(
        f"  {label}: raw {100 * raw:+.1f} pts; same 100-point band {100 * d:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}]"
    )
