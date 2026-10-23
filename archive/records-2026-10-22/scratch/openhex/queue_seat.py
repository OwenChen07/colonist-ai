"""Queue wait vs seat: does joining the queue early or late decide who moves first?

Usage: queue_seat.py [LOG]  (default data/openhex/vm-client.log)
Pairs each 'in the 1v1 queue' line with the next 'start:' line.
"""

import re
import sys
from collections import defaultdict
from datetime import datetime

LOG = sys.argv[1] if len(sys.argv) > 1 else "data/openhex/vm-client.log"
TS = re.compile(r"^(\w{3} \d+ \d\d:\d\d:\d\d [AP]M) ")
START = re.compile(
    r"\[(\w+)\] start: we are (\w+), order \['(\w+)', '(\w+)'\], \['([^']+)', '([^']+)'\]"
)


def ts(line):
    m = TS.match(line)
    return (
        datetime.strptime("2026 " + m.group(1), "%Y %b %d %I:%M:%S %p") if m else None
    )


rows = []
joined = None
for line in open(LOG):
    if "in the 1v1 queue" in line:
        joined = ts(line)
    m = START.search(line)
    if m and joined is not None:
        gid, color, o1, o2, n1, n2 = m.groups()
        wait = (ts(line) - joined).total_seconds()
        opp = n2 if n1.startswith("colonist-ai") else n1
        rows.append((gid, wait, color, o1 == color, opp))
        joined = None

print(f"games paired: {len(rows)}")
bands = [(0, 10), (10, 30), (30, 60), (60, 120), (120, 600), (600, 1e9)]
for lo, hi in bands:
    sel = [r for r in rows if lo <= r[1] < hi]
    if sel:
        first = sum(r[3] for r in sel)
        print(
            f"wait {lo:>4}-{hi if hi < 1e9 else 'inf':>4}s: n={len(sel):3d} first seat {first:3d} ({first / len(sel):.0%})"
        )
by_color = defaultdict(lambda: [0, 0])
for r in rows:
    by_color[r[2]][0] += 1
    by_color[r[2]][1] += r[3]
for c, (n, f) in sorted(by_color.items()):
    print(f"we are {c}: n={n} first seat {f} ({f / n:.0%})")
by_opp = defaultdict(lambda: [0, 0])
for r in rows:
    by_opp[r[4]][0] += 1
    by_opp[r[4]][1] += r[3]
for o, (n, f) in sorted(by_opp.items(), key=lambda x: -x[1][0]):
    print(f"vs {o:24s} n={n:3d} first seat {f:3d}")
first = sum(r[3] for r in rows)
print(f"all: first seat {first}/{len(rows)}")
short = sorted(r[1] for r in rows)
print("wait median", short[len(short) // 2], "s")
