"""Search speed from bridge.log 'move:' lines on stdin: sims per second for moves with >= 500 sims.

Splits the log at each net line ('  net ... epoch') so a net switch shows as its own segment.
"""

import re
import sys

MOVE = re.compile(r"\((?:win chance \d+%, )?(\d+) ms, (\d+) sims\)")
segs = []
cur = {"net": "(start)", "ms": 0, "sims": 0, "n": 0, "rates": []}
for line in sys.stdin:
    if (
        line.startswith("  net ")
        and "epoch" in line
        or "net " in line
        and "simulations, playing" in line
    ):
        if cur["n"]:
            segs.append(cur)
        cur = {"net": line.strip()[:110], "ms": 0, "sims": 0, "n": 0, "rates": []}
        continue
    m = MOVE.search(line)
    if m:
        ms, sims = int(m.group(1)), int(m.group(2))
        if sims >= 500 and ms > 0:
            cur["ms"] += ms
            cur["sims"] += sims
            cur["n"] += 1
            cur["rates"].append(sims * 1000 / ms)
if cur["n"]:
    segs.append(cur)
for s in segs[-6:]:
    r = sorted(s["rates"])
    print(
        f"{s['n']:6d} moves  {s['sims'] * 1000 / s['ms']:7.0f} sims/s overall  "
        f"median {r[len(r) // 2]:6.0f}  | {s['net']}"
    )
