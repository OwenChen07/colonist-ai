"""Search speed from a bridge.log line on (includes the game in progress).
Usage: rate_tail.py ACCT FROM_LINE [MIN_MS]
Median sims/s over moves that thought at least MIN_MS (default 1500)."""

import re
import statistics
import sys

ROOT = "/home/owen/colonist-ai/data"
MOVE = re.compile(r"  move: .* \(win chance \d+%, (\d+) ms, (\d+) sims\)")
acct, start = sys.argv[1], int(sys.argv[2])
min_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
rates = []
with open(f"{ROOT}/{acct}/bridge.log", errors="replace") as f:
    for i, line in enumerate(f, 1):
        if i < start:
            continue
        m = MOVE.match(line)
        if m and int(m.group(1)) >= min_ms and int(m.group(2)) > 0:
            rates.append(int(m.group(2)) / int(m.group(1)) * 1000)
if rates:
    q = statistics.quantiles(rates, n=10) if len(rates) > 9 else [0] * 9
    print(
        f"{acct} from line {start}: moves {len(rates)}  median sims/s {statistics.median(rates):.0f}  p10 {q[0]:.0f}  p90 {q[8]:.0f}"
    )
else:
    print(f"{acct} from line {start}: no moves")
