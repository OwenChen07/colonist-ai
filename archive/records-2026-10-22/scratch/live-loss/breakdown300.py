"""Loss breakdown refresh (Oct 21): last N ranked games per band across the live
accounts, index-only (length, end buildings, repeat opponents), with Wilson CIs.
Usage: breakdown300.py [N]"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import load_rows, wilson  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
ACC = ("bot3", "bot9", "bot10", "bot11", "bot12")

caps = {}
for a in ACC:
    for line in open(f"data/{a}/games.jsonl"):
        if line.strip():
            g = json.loads(line)
            caps[(a, g["at"])] = g.get("capture")
idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "ok":
            idx[r["capture"]] = r

rows = [r for r in load_rows() if r["a"] in ACC]
for r in rows:
    r["ix"] = idx.get(caps.get((r["a"], r["at"])))


def wr(sel):
    return wilson(sum(r["won"] for r in sel), len(sel))


def cmp(sel, side_key, name):
    out = defaultdict(list)
    for r in sel:
        b, h = r["ix"]["bot"].get(side_key), r["ix"]["human"].get(side_key)
        if b is None or h is None:
            continue
        out["more" if b > h else "fewer" if b < h else "same"].append(r)
    return f"  {name:12s} " + "  ".join(
        f"{k} {wr(out[k])}" for k in ("more", "same", "fewer")
    )


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def report(name, sel):
    sel = sel[-N:]
    ix = [r for r in sel if r["ix"] and r["ix"].get("bot") and r["ix"].get("human")]
    print(f"===== {name}: last {len(sel)} ranked, indexed {len(ix)}; overall {wr(sel)}")
    if sel:
        t = lambda x: (datetime.utcfromtimestamp(x) - timedelta(hours=4)).strftime(
            "%b %d %I:%M %p"
        )
        print(
            f"  Toronto {t(sel[0]['at'])} .. {t(sel[-1]['at'])}; accounts "
            + ", ".join(f"{a} {sum(r['a']==a for r in sel)}" for a in ACC)
        )
    for lo, hi in ((0, 60), (60, 75), (75, 90), (90, 999)):
        b = [r for r in ix if lo <= r["ix"]["rolls"] < hi]
        print(f"  rolls {lo:3d}-{hi:3d}: {wr(b)}")
    for k in ("cities", "settlements", "roads"):
        print(cmp(ix, k, k))
    print("  end buildings, mean (bot | person):")
    for won in (True, False):
        for lab, f in (("<75", lambda x: x < 75), ("75+", lambda x: x >= 75)):
            s = [r for r in ix if r["won"] == won and f(r["ix"]["rolls"])]
            parts = []
            for k in ("settlements", "cities", "roads", "dead_roads"):
                parts.append(
                    f"{k[:5]} {mean([r['ix']['bot'].get(k) for r in s]):.2f}|{mean([r['ix']['human'].get(k) for r in s]):.2f}"
                )
            vps = [
                2 * r["ix"]["bot"]["cities"] + r["ix"]["bot"]["settlements"] for r in s
            ]
            print(
                f"    {'win ' if won else 'loss'} {lab} n={len(s):3d}: "
                + ", ".join(parts)
                + f", bot board VP {mean(vps):.2f}"
            )
    # bot city count at the end of long losses
    s = [r for r in ix if not r["won"] and r["ix"]["rolls"] >= 75]
    dist = defaultdict(int)
    for r in s:
        dist[r["ix"]["bot"]["cities"]] += 1
    print(f"  long losses, bot cities at end: {dict(sorted(dist.items()))}")
    s = [r for r in ix if r["won"] and r["ix"]["rolls"] >= 75]
    dist = defaultdict(int)
    for r in s:
        dist[r["ix"]["bot"]["cities"]] += 1
    print(f"  long wins,   bot cities at end: {dict(sorted(dist.items()))}")
    for lab, f in (
        ("1st", lambda n: n == 1),
        ("2-3", lambda n: 2 <= n <= 3),
        ("4+", lambda n: n >= 4),
    ):
        print(f"  meeting {lab}: {wr([r for r in sel if r['nth'] and f(r['nth'])])}")
    for lo, hi in ((0, 1700), (1700, 1900), (1900, 2100), (2100, 2300), (2300, 9999)):
        print(
            f"  opp {lo}-{hi}: {wr([r for r in sel if r['opp_mmr'] is not None and lo <= r['opp_mmr'] < hi])}"
        )


report("all live accounts", rows)
report(
    "opponents 2100+",
    [r for r in rows if r["opp_mmr"] is not None and r["opp_mmr"] >= 2100],
)
