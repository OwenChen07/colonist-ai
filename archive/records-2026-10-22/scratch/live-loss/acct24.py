"""Last-24h ranked games per account: record, opponent and bot rating, seat
(first/second), length (rolls), dead roads, net and simulations. For the lead's
bot4 question (Oct 19). Aggregates only, no names.

    .venv/bin/python data/orchestrator/scratch/live-loss/acct24.py [HOURS] [END_EPOCH]
"""

import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.loss_report import wilson

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24
END = float(sys.argv[2]) if len(sys.argv) > 2 else time.time()
CACHE = json.loads(Path("data/orchestrator/analysis/opponent_ratings.json").read_text())

idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "ok":
            idx[r["capture"]] = r


def seat(path):
    try:
        record = convert(load_capture(path)).record
    except Exception:
        return None
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    return list(record["seats"]).index(you[0])


def mean_ci(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return "n<2"
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return f"{m:.1f} ± {1.96 * sd / math.sqrt(n):.1f} (n {n})"


rows = defaultdict(list)
for a in ACCOUNTS:
    for line in open(f"data/{a}/games.jsonl"):
        if not line.strip():
            continue
        g = json.loads(line)
        if g.get("queue") != "ranked" or g.get("own_bot"):
            continue
        if not (END - HOURS * 3600 <= g["at"] <= END):
            continue
        ix = idx.get(g["capture"])
        won = (
            ix["bot_won"]
            if ix and "bot_won" in ix
            else g.get("winner") == g.get("bot_color")
        )
        m = CACHE.get(g["capture"]) or {}
        rows[a].append(
            {
                "won": bool(won),
                "opp": m.get("opp_mmr"),
                "bot": m.get("bot_mmr"),
                "seat": seat(Path("captures") / a / g["capture"]),
                "rolls": ix.get("rolls") if ix else None,
                "dead": ix["bot"]["dead_roads"] if ix and "bot" in ix else None,
                "net": (g.get("round"), g.get("simulations"), g.get("net_sha256")),
                "ended": g.get("ended"),
            }
        )

allrows = [r for a in ACCOUNTS for r in rows[a]]
for a in list(ACCOUNTS) + ["others", "all"]:
    if a == "others":
        sel = [r for b in ACCOUNTS if b != "bot4" for r in rows[b]]
    elif a == "all":
        sel = allrows
    else:
        sel = rows[a]
    if not sel:
        print(a, "none")
        continue
    w = sum(r["won"] for r in sel)
    print(f"== {a}: {w}-{len(sel) - w} {wilson(w, len(sel))}")
    print(
        f"   opp rating {mean_ci([r['opp'] for r in sel])}  bot rating {mean_ci([r['bot'] for r in sel])}"
    )
    bands = Counter()
    bw = Counter()
    for r in sel:
        if r["opp"] is None:
            continue
        b = "<2100" if r["opp"] < 2100 else "2100-2300" if r["opp"] < 2300 else "2300+"
        bands[b] += 1
        bw[b] += r["won"]
    print(
        "   bands "
        + "  ".join(f"{b} {bw[b]}/{bands[b]}" for b in ("<2100", "2100-2300", "2300+"))
    )
    for s in (0, 1):
        ss = [r for r in sel if r["seat"] == s]
        if ss:
            print(
                f"   seat {'first' if s == 0 else 'second'}: {sum(r['won'] for r in ss)}/{len(ss)}"
            )
    print(
        f"   rolls {mean_ci([r['rolls'] for r in sel])}  long (90+ rolls) {sum(1 for r in sel if (r['rolls'] or 0) >= 90)}"
    )
    print(f"   rolls in losses {mean_ci([r['rolls'] for r in sel if not r['won']])}")
    print(f"   dead roads {mean_ci([r['dead'] for r in sel])}")
    print(f"   nets {Counter(r['net'] for r in sel).most_common(3)}")
    print(f"   ended {Counter(r['ended'] for r in sel).most_common(5)}")
    print(
        f"   missing ratings {sum(1 for r in sel if r['opp'] is None)}, seat {sum(1 for r in sel if r['seat'] is None)}"
    )
