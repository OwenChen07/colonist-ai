"""Split our OpenHex win/loss production gap into placement, dice luck and robber.

Per completed game and player, over the whole game:
  open  = pips of the two setup settlements (sum of 36ths per roll)
  exp   = sum over rolls of expected cards from the buildings standing then (pips/36)
  dice  = cards the actual rolls would give those buildings with no robber
  real  = cards actually produced (resourcesProduced)
dice - exp is dice luck; real - dice is robber (and bank shortage).

Usage: .venv/bin/python data/orchestrator/scratch/openhex/luck.py [--since HH:MM-UTC-mtime]
"""

import collections
import glob
import json
import os
import statistics as st
import sys

from catanatron.openhex.board import Board

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
since = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0

rows = []
for path in sorted(glob.glob("data/openhex/games/*.json")):
    if os.path.getmtime(path) < since:
        continue
    g = json.load(open(path))
    fs = g.get("final_state")
    if not fs or g.get("game", {}).get("status") != "completed" or not fs.get("winner"):
        continue
    if not g.get("tiles"):
        continue
    board = Board(g["tiles"], g["ports"])
    adj = board.catan_map.adjacent_tiles
    me = g["me"]
    them = [c for c in g["turn_order"] if c != me][0]
    mult = {me: collections.Counter(), them: collections.Counter()}  # node -> 1 or 2
    opening = collections.Counter()
    exp = collections.Counter()
    dice = collections.Counter()
    real = collections.Counter()
    for e in sorted(
        {e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"]
    ):
        ev = e["event"]
        t, p = ev["type"], ev.get("player")
        if t in ("setupSettlementPlaced", "settlementBuilt", "cityBuilt"):
            n = board.node(ev["vertex"])
            mult[p][n] += 1
            if t == "setupSettlementPlaced":
                opening[p] += sum(PIPS.get(tl.number, 0) for tl in adj[n])
        elif t == "diceRolled":
            roll = ev["die1"] + ev["die2"]
            for c in (me, them):
                for n, m in mult[c].items():
                    for tl in adj[n]:
                        if tl.number is None:
                            continue
                        exp[c] += m * PIPS[tl.number] / 36
                        if tl.number == roll:
                            dice[c] += m
        elif t == "resourcesProduced":
            for c, r in ev["production"].items():
                real[c] += sum(r.values())
    won = fs["winner"] == me
    rows.append(
        dict(
            won=won,
            **{
                k: (v[me], v[them])
                for k, v in (
                    ("open", opening),
                    ("exp", exp),
                    ("dice", dice),
                    ("real", real),
                )
            },
        )
    )


def show(name, rs):
    if not rs:
        return
    m = lambda k, i: st.mean(r[k][i] for r in rs)
    print(f"{name}: n={len(rs)}")
    for k in ("open", "exp", "dice", "real"):
        print(
            f"  {k:5s} us {m(k, 0):6.1f}  them {m(k, 1):6.1f}  diff {m(k, 0) - m(k, 1):+6.1f}"
        )
    print(
        f"  dice luck (dice-exp) us {m('dice', 0) - m('exp', 0):+5.1f} them {m('dice', 1) - m('exp', 1):+5.1f}"
    )
    print(
        f"  robber etc (real-dice) us {m('real', 0) - m('dice', 0):+5.1f} them {m('real', 1) - m('dice', 1):+5.1f}"
    )


show("lost", [r for r in rows if not r["won"]])
show("won", [r for r in rows if r["won"]])
show("all", rows)
