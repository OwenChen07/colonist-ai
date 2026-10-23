"""Roads, settlements, cities, dev cards and Longest Road by seat, for E13.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/roads_seat.py
"""

import collections
import glob
import json
import statistics as st

rows = []
types = collections.Counter()
for path in sorted(glob.glob("data/openhex/games/*.json")):
    g = json.load(open(path))
    fs = g.get("final_state")
    if not fs or g.get("game", {}).get("status") != "completed" or not fs.get("winner"):
        continue
    me = g["me"]
    them = [c for c in g["turn_order"] if c != me][0]
    opp = [p["name"] for p in g["game"]["players"] if p["color"] != me][0]
    c = {k: collections.Counter() for k in ("road", "sett", "city", "dev")}
    lr = None
    for e in g["events"]:
        ev = e["event"]
        t, p = ev["type"], ev.get("player")
        types[t] += 1
        if t in ("roadBuilt", "setupRoadPlaced", "freeRoadPlaced"):
            c["road"][p] += 1
        elif t == "settlementBuilt":
            c["sett"][p] += 1
        elif t == "cityBuilt":
            c["city"][p] += 1
        elif t == "devCardBought":
            c["dev"][p] += 1
        elif t == "longestRoadAwarded":
            lr = p
    rows.append(
        dict(
            won=fs["winner"] == me,
            first=g["turn_order"][0] == me,
            ar=opp.startswith("ar-"),
            lr={me: "us", them: "them"}.get(lr, "none"),
            **{k: (v[me], v[them]) for k, v in c.items()},
        )
    )

print("road-ish event types:", {t: n for t, n in types.items() if "oad" in t})


def show(name, rs):
    if not rs:
        return

    def m(k, i):
        return f"{st.mean(r[k][i] for r in rs):4.1f}"

    lrc = collections.Counter(r["lr"] for r in rs)
    print(
        f"{name:28s} n={len(rs):3d} won={sum(r['won'] for r in rs):3d}"
        f"  roads {m('road', 0)}/{m('road', 1)}  setts {m('sett', 0)}/{m('sett', 1)}"
        f"  cities {m('city', 0)}/{m('city', 1)}  devs {m('dev', 0)}/{m('dev', 1)}"
        f"  LR us/them/none {lrc['us']}/{lrc['them']}/{lrc['none']}"
    )


for opp_name, of in (
    ("all", lambda r: True),
    ("ar bots", lambda r: r["ar"]),
    ("others", lambda r: not r["ar"]),
):
    for seat_name, sf in (
        ("1st", lambda r: r["first"]),
        ("2nd", lambda r: not r["first"]),
    ):
        rs = [r for r in rows if of(r) and sf(r)]
        show(f"{opp_name} {seat_name}", rs)
        show(f"{opp_name} {seat_name} won", [r for r in rs if r["won"]])
        show(f"{opp_name} {seat_name} lost", [r for r in rs if not r["won"]])
