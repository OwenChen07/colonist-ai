"""Compare completed games by opponent group: seat, length, public VP over time,
dev cards, knights, production, bank trades.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/losses.py
"""

import collections
import glob
import json
import statistics as st

STRONG = {"hexn-4d.0", "ar-PXns-a"}
rows = []
for path in sorted(glob.glob("data/openhex/games/*.json")):
    g = json.load(open(path))
    fs = g.get("final_state")
    if not fs or g.get("game", {}).get("status") != "completed" or not fs.get("winner"):
        continue
    me = g["me"]
    opp = [p["name"] for p in g["game"]["players"] if p["color"] != me][0]
    them = [c for c in g["turn_order"] if c != me][0]
    vp = {me: 0, them: 0}
    la = lr = None
    turn = 0
    snap = {}
    prod = collections.Counter()
    bought = collections.Counter()
    knights = collections.Counter()
    trades = collections.Counter()
    discards = collections.Counter()
    first_city = {}
    for e in sorted(g["events"], key=lambda e: e["seq"]):
        ev = e["event"]
        t, p = ev["type"], ev.get("player")
        if t in ("setupSettlementPlaced", "settlementBuilt"):
            vp[p] += 1
        elif t == "cityBuilt":
            vp[p] += 1
            first_city.setdefault(p, turn)
        elif t == "largestArmyAwarded":
            if la:
                vp[la] -= 2
            la = p
            vp[p] += 2
        elif t == "longestRoadAwarded":
            if lr:
                vp[lr] -= 2
            lr = p
            vp[p] += 2
        elif t == "turnEnded":
            turn = ev["turnNumber"]
            for mark in (20, 40, 60):
                if turn == mark:
                    snap[mark] = (vp[me], vp[them])
                    snap[f"p{mark}"] = (prod[me], prod[them])
        elif t == "resourcesProduced":
            for c, r in ev["production"].items():
                prod[c] += sum(r.values())
        elif t == "devCardBought":
            bought[p] += 1
        elif t == "knightPlayed":
            knights[p] += 1
        elif t == "bankTradeExecuted":
            trades[p] += 1
        elif t == "resourcesDiscarded":
            discards[p] += sum(ev["resources"].values())
    won = fs["winner"] == me
    rows.append(
        dict(
            gid=g["id"][:8],
            opp=opp,
            won=won,
            first=g["turn_order"][0] == me,
            turns=fs["turnNumber"],
            snap=snap,
            vp=(vp[me], vp[them]),
            prod=(prod[me], prod[them]),
            bought=(bought[me], bought[them]),
            knights=(knights[me], knights[them]),
            trades=(trades[me], trades[them]),
            disc=(discards[me], discards[them]),
            city=(first_city.get(me), first_city.get(them)),
            la=la == me,
            lr=lr == me,
        )
    )


def show(name, rs):
    if not rs:
        return

    def m(f):
        vals = [f(r) for r in rs if f(r) is not None]
        return f"{st.mean(vals):5.1f}" if vals else "  -  "

    print(
        f"{name}: n={len(rs)} wins={sum(r['won'] for r in rs)} first-seat={sum(r['first'] for r in rs)}"
    )
    print(
        f"  turns {m(lambda r: r['turns'])}  prod us/them {m(lambda r: r['prod'][0])}/{m(lambda r: r['prod'][1])}"
        f"  devs bought {m(lambda r: r['bought'][0])}/{m(lambda r: r['bought'][1])}"
        f"  knights {m(lambda r: r['knights'][0])}/{m(lambda r: r['knights'][1])}"
        f"  bank trades {m(lambda r: r['trades'][0])}/{m(lambda r: r['trades'][1])}"
        f"  discarded {m(lambda r: r['disc'][0])}/{m(lambda r: r['disc'][1])}"
    )
    print(
        f"  first city turn {m(lambda r: r['city'][0])}/{m(lambda r: r['city'][1])}"
        f"  LA ours {sum(r['la'] for r in rs)}  LR ours {sum(r['lr'] for r in rs)}"
        + "".join(
            f"  VP@{k} {m(lambda r, k=k: r['snap'].get(k, (None,))[0])}/"
            f"{m(lambda r, k=k: r['snap'][k][1] if k in r['snap'] else None)}"
            for k in (20, 40, 60, "p20", "p40", "p60")
        )
    )


for r in rows:
    print(
        r["gid"],
        r["opp"],
        "W" if r["won"] else "L",
        "1st" if r["first"] else "2nd",
        r["turns"],
        "vp",
        r["vp"],
        "prod",
        r["prod"],
        "devs",
        r["bought"],
        "kn",
        r["knights"],
        "city",
        r["city"],
        "LA" if r["la"] else "",
        "LR" if r["lr"] else "",
    )
print()
show("strong, lost", [r for r in rows if r["opp"] in STRONG and not r["won"]])
show("strong, won", [r for r in rows if r["opp"] in STRONG and r["won"]])
show("others, lost", [r for r in rows if r["opp"] not in STRONG and not r["won"]])
show("others, won", [r for r in rows if r["opp"] not in STRONG and r["won"]])
show("all lost", [r for r in rows if not r["won"]])
show("all won", [r for r in rows if r["won"]])
