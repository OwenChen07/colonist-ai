"""Per game in a bridge.log (split at 'game started'): moves, median sims/s over moves
of at least MIN_MS, first-settlement sims, and the last win chance. Prints the last N.
Usage: per_game_rate.py ACCT [N] [MIN_MS]"""

import re
import statistics
import sys

acct = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 70
min_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 4500
MOVE = re.compile(r"  move: (.*) \(win chance (\d+)%, (\d+) ms, (\d+) sims\)")
games = []
cur = None
for i, line in enumerate(
    open(f"/home/owen/colonist-ai/data/{acct}/bridge.log", errors="replace"), 1
):
    if "following: game started" in line:
        cur = {"line": i, "rates": [], "moves": 0, "first": None, "wc": [], "slow": 0}
        games.append(cur)
        continue
    m = MOVE.match(line)
    if m and cur is not None:
        what, wc, ms, sims = (
            m.group(1),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4)),
        )
        cur["moves"] += 1
        cur["wc"].append(wc)
        if cur["first"] is None and what.startswith("settlement"):
            cur["first"] = sims
        if ms >= min_ms and sims:
            cur["rates"].append(sims / ms * 1000)
        if ms > 14000 and not what.startswith("settlement"):
            cur["slow"] += 1
for g in games[-n:]:
    r = statistics.median(g["rates"]) if g["rates"] else 0
    peak = max(g["wc"]) if g["wc"] else 0
    print(
        f"line {g['line']:>7} moves {g['moves']:>4} sims/s {r:5.0f} first {g['first']} "
        f"max wc {peak} last wc {g['wc'][-1] if g['wc'] else None} >14s {g['slow']}"
    )
