"""Seconds per move from each account's bridge.log 'move:' lines (last N moves)."""

import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/owen/colonist-ai/data")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
LINE = re.compile(r"move: (.+?) \(win chance \d+%, (\d+) ms, (\d+) sims\)")

for account in sys.argv[2:] or ["bot3", "bot8", "bot9", "bot10"]:
    path = ROOT / account / "bridge.log"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        print(f"{account}: no log")
        continue
    moves = [
        (m.group(1), int(m.group(2)), int(m.group(3)))
        for line in lines[-200000:]
        for m in [LINE.search(line)]
        if m
    ]
    moves = moves[-N:]
    if not moves:
        print(f"{account}: no moves")
        continue
    ms = [m[1] for m in moves]
    instant = [m for m in moves if m[2] == 0]
    kinds = Counter(m[0].split(" (")[0].split(",")[0].split(" to ")[0] for m in instant)
    searched = [m[1] for m in moves if m[2] > 0]
    print(
        f"{account}: {len(moves)} moves, mean {statistics.mean(ms)/1000:.2f} s, "
        f"median {statistics.median(ms)/1000:.2f} s; searched moves mean "
        f"{statistics.mean(searched)/1000:.2f} s; instant (0 sims) {len(instant)} "
        f"({100*len(instant)/len(moves):.0f}%), under 0.5 s {sum(x < 500 for x in ms)} "
        f"({100*sum(x < 500 for x in ms)/len(moves):.0f}%); instant kinds {kinds.most_common(6)}"
    )
