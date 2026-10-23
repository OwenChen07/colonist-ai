"""First-seat win rate in the pulled public games (replays have the turn order),
and in our own games by model (r66 before the Oct 18 9:24 AM cutoff, r73 after).

Usage: seat.py [cutoff mtime]
"""

import glob
import json
import os
import sys

R = "/home/owen/colonist-ai/data/openhex/opponents/replays"
n = k = 0
for path in glob.glob(f"{R}/*.json"):
    d = json.load(open(path))
    order = d.get("turnOrder")
    w = next(
        (
            p.get("color")
            for p in d.get("players", [])
            if p.get("botId") == d.get("winnerBotId")
        ),
        None,
    )
    if not order or not w:
        continue
    n += 1
    k += order[0] == w
print(
    f"public replays: first seat won {k}/{n}"
    if n
    else f"public replays: no turn order found; keys {sorted(d)[:20]}"
)

cut = float(sys.argv[1]) if len(sys.argv) > 1 else 0
for label, keep in (("r66", lambda t: t < cut), ("r73", lambda t: t >= cut)):
    f = fw = s = sw = 0
    for path in glob.glob("data/openhex/games/*.json"):
        if not keep(os.path.getmtime(path)):
            continue
        g = json.load(open(path))
        fs = g.get("final_state")
        if (
            not fs
            or not fs.get("winner")
            or g.get("game", {}).get("status") != "completed"
        ):
            continue
        won = fs["winner"] == g["me"]
        if g["turn_order"][0] == g["me"]:
            f += 1
            fw += won
        else:
            s += 1
            sw += won
    print(f"{label}: we 1st {fw}/{f}, we 2nd {sw}/{s}")
