"""Lead's Oct 19, 9 PM question: is bot5's lead (opponent model since Oct 12,
10:13 PM Toronto) explained by opponents? bot5 vs bot3/bot4/bot7, by opponent
rating band, mean opponent rating, repeat opponents (meeting number per
account) and game length. No names: opponents are sha256(name)[:8].

    .venv/bin/python data/orchestrator/scratch/live-loss/bot5_gap.py
"""

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

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
TZ = ZoneInfo("America/Toronto")
E2_START = datetime(2026, 10, 12, 22, 13, tzinfo=TZ).timestamp()
OCT17 = datetime(2026, 10, 17, 0, 0, tzinfo=TZ).timestamp()
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


def scan(path):
    """Opponent hash and both MMRs from the raw frames."""
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
    for line in open(f"data/{a}/games.jsonl"):
        if not line.strip():
            continue
        g = json.loads(line)
        if g.get("queue") != "ranked" or g.get("own_bot") or g.get("winner") is None:
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

# meeting number per account, over the account's whole history
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
            "seat": g.get("bot_color"),
        }
    )


def band_rates(sel):
    out = {}
    for lo, hi in BANDS:
        b = [r for r in sel if r["opp_mmr"] is not None and lo <= r["opp_mmr"] < hi]
        out[(lo, hi)] = (sum(r["won"] for r in b), len(b))
    return out


def standardized(b5, oth):
    """Others' band win rates, weighted by bot5's band mix; bootstrap CI of
    bot5 minus that."""

    def diff(x, y):
        n5 = sum(v[1] for v in x.values())
        num = 0
        for k, (w, n) in x.items():
            if n and y[k][1]:
                num += n * (w / n - y[k][0] / y[k][1])
        return num / n5

    d = diff(b5, oth)
    rng = random.Random(1)
    boots = []
    for _ in range(2000):
        bx = {
            k: (sum(rng.random() < w / n for _ in range(n)), n) if n else (0, 0)
            for k, (w, n) in b5.items()
        }
        by = {
            k: (sum(rng.random() < w / n for _ in range(n)), n) if n else (0, 0)
            for k, (w, n) in oth.items()
        }
        boots.append(diff(bx, by))
    boots.sort()
    return d, boots[50], boots[1949]


def fmt_t(t):
    return datetime.fromtimestamp(t, TZ).strftime("%b %d %I:%M %p")


for label, start in (
    ("since Oct 12, 10:13 PM (E2 on bot5)", E2_START),
    ("since Oct 17", OCT17),
):
    win = [r for r in rows if r["at"] >= start]
    print(
        f"\n=== {label}: {len(win)} games, {fmt_t(win[0]['at'])} .. {fmt_t(win[-1]['at'])}"
    )
    groups = {
        "bot5": [r for r in win if r["a"] == "bot5"],
        "others": [r for r in win if r["a"] != "bot5"],
    }
    for a in ("bot3", "bot4", "bot7"):
        groups[a] = [r for r in win if r["a"] == a]
    for name, sel in groups.items():
        rated = [r for r in sel if r["opp_mmr"] is not None]
        mo = sum(r["opp_mmr"] for r in rated) / max(1, len(rated))
        mb = sum(r["bot_mmr"] for r in rated if r["bot_mmr"]) / max(1, len(rated))
        print(
            f"{name:>7}: all {wilson(sum(r['won'] for r in sel), len(sel))}  rated {len(rated)}  "
            f"opp mean {mo:.0f}  bot mean {mb:.0f}"
        )
        print(
            "         bands: "
            + "; ".join(
                f"{lo}-{hi if hi < 9999 else ''}: {wilson(*band_rates(sel)[(lo, hi)])}"
                for lo, hi in BANDS
            )
        )
    b5, oth = band_rates(groups["bot5"]), band_rates(groups["others"])
    d, lo, hi = standardized(b5, oth)
    raw = sum(r["won"] for r in groups["bot5"]) / len(groups["bot5"]) - sum(
        r["won"] for r in groups["others"]
    ) / len(groups["others"])
    print(
        f"gap bot5 - others: raw {100 * raw:+.1f} pts; same-band (others reweighted to bot5's band mix) "
        f"{100 * d:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}]"
    )
    # finer: 50-point bands for the standardization
    fine = [(x, x + 100) for x in range(1500, 2800, 100)]

    def fb(sel):
        return {
            k: (
                sum(
                    r["won"]
                    for r in sel
                    if r["opp_mmr"] is not None and k[0] <= r["opp_mmr"] < k[1]
                ),
                sum(
                    1
                    for r in sel
                    if r["opp_mmr"] is not None and k[0] <= r["opp_mmr"] < k[1]
                ),
            )
            for k in fine
        }

    d2, lo2, hi2 = standardized(fb(groups["bot5"]), fb(groups["others"]))
    print(
        f"  same 100-point band: {100 * d2:+.1f} pts [{100 * lo2:+.1f}, {100 * hi2:+.1f}]"
    )
    print("repeat opponents (meeting number with that account):")
    for name in ("bot5", "others"):
        sel = [r for r in groups[name] if r["nth"]]
        first = [r for r in sel if r["nth"] == 1]
        rep = [r for r in sel if r["nth"] >= 2]
        rep4 = [r for r in sel if r["nth"] >= 4]
        print(
            f"  {name:>6}: share repeat {100 * len(rep) / max(1, len(sel)):.0f}% of {len(sel)};  "
            f"1st {wilson(sum(r['won'] for r in first), len(first))};  "
            f"2nd+ {wilson(sum(r['won'] for r in rep), len(rep))};  "
            f"4th+ {wilson(sum(r['won'] for r in rep4), len(rep4))}"
        )
    print("game length (dice rolls, replayed games only):")
    for name in ("bot5", "others"):
        sel = [r for r in groups[name] if r["rolls"]]
        for wl, keep in (("wins", True), ("losses", False)):
            x = sorted(r["rolls"] for r in sel if r["won"] == keep)
            if x:
                print(
                    f"  {name:>6} {wl}: n {len(x)} mean {sum(x) / len(x):.1f} median {x[len(x) // 2]}"
                )
        long = [r for r in sel if r["rolls"] >= 90]
        print(
            f"  {name:>6}: 90+ rolls {len(long)}/{len(sel)}, win {wilson(sum(r['won'] for r in long), len(long))}"
        )
    print(
        "rounds: "
        + ", ".join(
            f"{n}: "
            + str(
                sorted(
                    {
                        (r["round"], sum(1 for q in s if q["round"] == r["round"]))
                        for r in s
                    }
                )
            )
            for n, s in (("bot5", groups["bot5"]), ("others", groups["others"]))
        )
    )
