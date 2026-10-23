"""Per-host self-play games and games/s, from each round log's last '| N games, R/s' line.

Usage: host_rates.py <round> [<round> ...]
"""

import os
import re
import sys

base = os.path.expanduser("~/colonist-ai/runs/g1")
rounds = list(map(int, sys.argv[1:]))
rows = {}
for r in rounds:
    d = os.path.join(base, "round_%03d" % r)
    for f in sorted(os.listdir(d)):
        if not f.endswith(".log") or f == "prefetch.log":
            continue
        host = f[len("remote-") : -4] if f.startswith("remote-") else "here"
        last = None
        for line in open(os.path.join(d, f), errors="replace"):
            m = re.search(r"\| (\d+) games, ([\d.]+)/s", line)
            if m:
                last = (int(m.group(1)), float(m.group(2)))
        if last:
            rows.setdefault(host, {})[r] = last
print("host".ljust(24) + "".join(("r%d games, /s" % r).rjust(18) for r in rounds))
tot = {r: [0, 0.0] for r in rounds}
for host in sorted(rows):
    cells = []
    for r in rounds:
        v = rows[host].get(r)
        if v:
            tot[r][0] += v[0]
            tot[r][1] += v[1]
        cells.append(("%d, %.2f" % v if v else "-").rjust(18))
    print(host.ljust(24) + "".join(cells))
print(
    "total".ljust(24) + "".join(("%d, %.2f" % tuple(tot[r])).rjust(18) for r in rounds)
)
