"""Our games by seat: opening pips (each settlement), ore+wheat / wood+brick pips,
VP at turns 20/40/60, final settlements/cities, win rate.

Usage: seat_open.py [cutoff mtime]  (games at or after the cutoff only)
"""

import collections
import glob
import json
import os
import statistics as st
import sys

from catanatron.openhex.board import Board

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
cut = float(sys.argv[1]) if len(sys.argv) > 1 else 0
rows = collections.defaultdict(list)
for path in glob.glob("data/openhex/games/*.json"):
    if os.path.getmtime(path) < cut:
        continue
    g = json.load(open(path))
    fs = g.get("final_state")
    if (
        not fs
        or not fs.get("winner")
        or g.get("game", {}).get("status") != "completed"
        or not g.get("tiles")
    ):
        continue
    me = g["me"]
    them = [c for c in g["turn_order"] if c != me][0]
    board = Board(g["tiles"], g["ports"])
    adj = board.catan_map.adjacent_tiles
    seat = "1st" if g["turn_order"][0] == me else "2nd"
    sets = collections.defaultdict(list)
    mix = collections.defaultdict(collections.Counter)
    vp = collections.Counter()
    snap = {}
    la = lr = None
    for e in sorted(
        {e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"]
    ):
        ev = e["event"]
        t, p = ev["type"], ev.get("player")
        if t == "setupSettlementPlaced":
            n = board.node(ev["vertex"])
            sets[p].append(sum(PIPS.get(tl.number, 0) for tl in adj[n]))
            for tl in adj[n]:
                if tl.resource:
                    mix[p][tl.resource] += PIPS.get(tl.number, 0)
        if t in ("setupSettlementPlaced", "settlementBuilt", "cityBuilt"):
            vp[p] += 1
        elif t in ("largestArmyAwarded", "longestRoadAwarded"):
            old = la if t[0] == "l" and t.startswith("largest") else lr
            if old:
                vp[old] -= 2
            vp[p] += 2
            if t.startswith("largest"):
                la = p
            else:
                lr = p
        elif t == "turnEnded" and ev["turnNumber"] in (20, 40, 60):
            snap[ev["turnNumber"]] = vp[me] - vp[them]
    rows[seat].append(
        dict(
            won=fs["winner"] == me,
            us=sets[me],
            them=sets[them],
            us_ow=mix[me]["ORE"] + mix[me]["WHEAT"],
            them_ow=mix[them]["ORE"] + mix[them]["WHEAT"],
            us_wb=mix[me]["WOOD"] + mix[me]["BRICK"],
            them_wb=mix[them]["WOOD"] + mix[them]["BRICK"],
            snap=snap,
            turns=fs["turnNumber"],
        )
    )


def m(xs):
    xs = [x for x in xs if x is not None]
    return f"{st.mean(xs):.1f}" if xs else "-"


for seat, rs in sorted(rows.items()):
    print(
        f"we {seat}: n={len(rs)} won {sum(r['won'] for r in rs)}  turns {m([r['turns'] for r in rs])}"
    )
    print(
        f"  opening pips 1st/2nd settlement: us {m([r['us'][0] for r in rs])}/{m([r['us'][1] for r in rs])}"
        f"  them {m([r['them'][0] for r in rs])}/{m([r['them'][1] for r in rs])}"
    )
    print(
        f"  ore+wheat pips us/them {m([r['us_ow'] for r in rs])}/{m([r['them_ow'] for r in rs])}"
        f"  wood+brick us/them {m([r['us_wb'] for r in rs])}/{m([r['them_wb'] for r in rs])}"
    )
    print(
        "  VP lead (public) at turn 20/40/60: "
        + " / ".join(m([r["snap"].get(k) for r in rs]) for k in (20, 40, 60))
    )
    for res, sub in (
        ("won", [r for r in rs if r["won"]]),
        ("lost", [r for r in rs if not r["won"]]),
    ):
        print(
            f"    {res} n={len(sub)} lead@20/40/60 "
            + " / ".join(m([r["snap"].get(k) for r in sub]) for k in (20, 40, 60))
            + f"  open us {m([sum(r['us']) for r in sub])} them {m([sum(r['them']) for r in sub])}"
        )
