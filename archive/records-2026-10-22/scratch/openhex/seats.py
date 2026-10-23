"""Round 152's record (games after round 73's final 172) split by our seat and opponent."""

import collections
import glob
import json
import os

GAMES = "/home/owen/colonist-ai/data/openhex/games"
done = []
for path in sorted(glob.glob(f"{GAMES}/*.json"), key=os.path.getmtime):
    rec = json.load(open(path))
    g = rec.get("game", {})
    if g.get("status") == "active":
        continue
    done.append(rec)
import sys

tally = collections.defaultdict(lambda: [0, 0])
E86_START = (
    277  # round 73's 172, then round 152's 105 (E73's 100 plus 5 before the switch)
)
E87_START = 313  # step 50's 36 (E86, cut early; the last one 9664014b)
SPANS = {
    "r73": (0, 172),
    "r152": (172, 272),
    "r152all": (172, E86_START),
    "e86": (E86_START, E87_START),
}
lo, hi = next((SPANS[a] for a in sys.argv[1:] if a in SPANS), (E87_START, None))
for rec in done[lo:hi]:
    g = rec["game"]
    me = rec.get("me")
    w = g.get("winner")
    w = w.get("color") if isinstance(w, dict) else w
    seat = next(
        (p.get("seat") for p in g.get("players", []) if p.get("color") == me), "?"
    )
    opp = ",".join(p["name"] for p in g.get("players", []) if p.get("color") != me)
    t = tally[(opp, seat)]
    t[0] += w == me
    t[1] += 1
for (opp, seat), (k, n) in sorted(tally.items()):
    print(f"{opp} seat {seat}: {k}-{n - k} ({n} games)")
for s in sorted({seat for _, seat in tally}, key=str):
    k = sum(t[0] for (_, x), t in tally.items() if x == s)
    n = sum(t[1] for (_, x), t in tally.items() if x == s)
    print(f"seat {s} (0 = first): {k}-{n - k}")
print(
    "total:", sum(t[0] for t in tally.values()), "/", sum(t[1] for t in tally.values())
)
