"""Robber moves over a threshold since a bridge.log line, with the move before
(a roll = a 7, 15 s robber timer; a knight = inside the turn timer)."""

import re
import sys

ACCT, START, THR = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
MS = re.compile(r", (\d+) ms(?:, (\d+) sims)?\)")
prev, n = "", 0
with open(f"/home/owen/colonist-ai/data/{ACCT}/bridge.log", errors="replace") as f:
    for i, line in enumerate(f, 1):
        if i < START or "move: " not in line:
            continue
        mv = line.split("move: ", 1)[1].strip()
        m = MS.search(mv)
        if mv.startswith("robber") and m:
            n += 1
            if int(m.group(1)) > THR:
                kind = (
                    "after 7"
                    if prev.startswith("roll")
                    else "after knight" if "knight" in prev else prev[:20]
                )
                print(f"{i}: {kind:13} {m.group(1)} ms {m.group(2)} sims")
        prev = mv
print(n, "robber moves")
