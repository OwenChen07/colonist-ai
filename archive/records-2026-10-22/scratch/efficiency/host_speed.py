"""Per-host self-play seconds per game (per process), across rounds, to see
whether a host's speed is stable (measured weights would help) or noise
(only a live queue would)."""

import glob
import os
import re
import sys

rounds = sys.argv[1:] or ["078", "079", "080", "081", "082"]
pat = re.compile(r"(\d+) games in (\d+) s")
table = {}
for r in rounds:
    for path in glob.glob(f"runs/g1/round_{r}/remote-*.log"):
        host = os.path.basename(path)[7:-4]
        games = secs = 0
        for line in open(path):
            m = pat.search(line)
            if m and "|" in line:
                games += int(m.group(1))
                secs += int(m.group(2))
        if games:
            table.setdefault(host, {})[r] = secs / games
print("host".ljust(26) + "".join(r.rjust(7) for r in rounds))
for host in sorted(table):
    row = table[host]
    print(host.ljust(26) + "".join(f"{row.get(r, 0):7.1f}" for r in rounds))
