"""Win-chance curve per game from each account's bridge.log (lines 'move: ... (win chance N%, ...)'
between 'game started' and 'game saved to .../<capture>'). Prints per game: n moves, max, min,
first move after which it stayed below 50, biggest drop between consecutive bot moves.
"""

import re
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from phantom import TARGETS  # noqa: E402

PAT = re.compile(r"move: (.*) \(win chance (\d+)%")


def curve(account, capture):
    moves = []
    with open(f"data/{account}/bridge.log", errors="replace") as f:
        for line in f:
            if "game started" in line:
                moves = []
            m = PAT.search(line)
            if m:
                moves.append((m.group(1), int(m.group(2))))
            if "game saved to" in line and line.rstrip().endswith(capture):
                return moves
    return None


for key in TARGETS:
    a, cap = key.split("/")
    mv = curve(a, cap)
    if not mv:
        print(key, "no curve")
        continue
    w = [x for _, x in mv]
    drops = sorted(((w[i] - w[i + 1], i + 1) for i in range(len(w) - 1)), reverse=True)[
        :3
    ]
    peak = max(range(len(w)), key=lambda i: w[i])
    print(
        f"{key}: {len(w)} bot moves, start {w[0]}, max {w[peak]} at move {peak + 1}, end {w[-1]}"
    )
    print("   curve every 5th:", w[::5])
    for d, i in drops:
        print(
            f"   drop -{d} into move {i + 1}: {mv[i - 1][0]} ({w[i - 1]}) -> {mv[i][0]} ({w[i]})"
        )
    above = [i for i, x in enumerate(w) if x >= 60]
    if above and w[-1] < 50:
        print(f"   was >=60 at {len(above)} moves; last time at move {above[-1] + 1}")
