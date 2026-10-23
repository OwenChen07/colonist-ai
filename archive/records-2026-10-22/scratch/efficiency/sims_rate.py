"""Sims per second per bridge game, from the coordinator's copies of bridge.log."""

import re
import statistics
import sys
from pathlib import Path

MOVE = re.compile(r"move: .*\((?:win chance \d+%, )?(\d+) ms, (\d+) sims\)")
SAVE = re.compile(r"bridge-(\d{8})-(\d{6})\.json")
CUT = sys.argv[1] if len(sys.argv) > 1 else "20261019-183500"
SINCE = sys.argv[2] if len(sys.argv) > 2 else "20261019-120000"

ACCTS = (
    sys.argv[3].split(",")
    if len(sys.argv) > 3
    else ["bot3", "bot4", "bot8", "bot5", "bot7"]
)

for acct in ACCTS:
    path = Path.home() / "colonist-ai/data" / acct / "bridge.log"
    games = []  # (stamp, [rates])
    cur = []
    for line in path.read_text(errors="replace").splitlines():
        m = MOVE.search(line)
        if m:
            ms, sims = int(m[1]), int(m[2])
            if ms >= 800 and sims >= 50:
                cur.append((sims / ms * 1000, ms))
            continue
        s = SAVE.search(line)
        if s:
            stamp = f"{s[1]}-{s[2]}"
            if cur:
                games.append((stamp, cur))
            cur = []
    if cur:
        games.append(("99999999-current", cur))
    before = [g for g in games if SINCE <= g[0] < CUT]
    after = [g for g in games if g[0] >= CUT]
    for label, gs in (("before", before), ("after", after)):
        rates = [r for _, mv in gs for r, ms in mv]
        long_ = [r for _, mv in gs for r, ms in mv if ms >= 3000]
        if not rates:
            print(f"{acct} {label}: no games")
            continue
        print(
            f"{acct} {label}: {len(gs)} games, {len(rates)} moves, "
            f"median {statistics.median(rates):.0f} sims/s, "
            f"long moves (>=3 s) median {statistics.median(long_) if long_ else 0:.0f} sims/s"
        )
    for stamp, mv in games[-8:]:
        print(
            f"   {stamp}: median {statistics.median(r for r, _ in mv):.0f} sims/s over {len(mv)} moves"
        )
