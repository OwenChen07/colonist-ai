"""Audit item 3: after a robber move, what colonist logs, and how long until
the next log entry. Usage: audit_robber.py [N_CAPTURES]

For each robber move (log type 11, pieceEnum 5): the 139 entry's playerCount
in the same diff, whether a steal entry (14/15/74) came in that diff or later,
the seconds until the next log entry of another kind, and who moved it.
"""

import collections
import glob
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import game_start, DIFF

STEALS = {14, 15, 74}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    paths = sorted(glob.glob("captures/*/bridge-*.json"))
    random.Random(4).shuffle(paths)
    table = collections.defaultdict(lambda: [0, []])
    for p in paths[:n]:
        frames = load_capture(p)
        start = game_start(frames)
        if start is None:
            continue
        you = start.data["data"]["payload"].get("playerColor")
        pending = None  # (who, playerCount, t, steal_same_diff)
        for f in frames:
            if f.dir != "in" or f.seq <= start.seq or f.type != DIFF or not f.t:
                continue
            logs = f.data["data"]["payload"].get("diff", {}).get("gameLogState") or {}
            texts = [
                v.get("text")
                for _, v in sorted(logs.items(), key=lambda kv: int(kv[0]))
                if isinstance(v, dict) and isinstance(v.get("text"), dict)
            ]
            for t in texts:
                kind = t.get("type")
                if kind == 11 and t.get("pieceEnum") == 5:
                    count = next(
                        (u.get("playerCount") for u in texts if u.get("type") == 139),
                        None,
                    )
                    pending = [
                        "bot" if t.get("playerColor") == you else "opp",
                        count,
                        f.t,
                    ]
                elif pending is not None and kind in STEALS:
                    key = (pending[0], pending[1], f"steal {kind}")
                    table[key][0] += 1
                    table[key][1].append((f.t - pending[2]) / 1000)
                    pending = None
                elif pending is not None and kind not in (139, 11):
                    key = (pending[0], pending[1], f"no steal, next {kind}")
                    table[key][0] += 1
                    table[key][1].append((f.t - pending[2]) / 1000)
                    pending = None
    for key, (k, waits) in sorted(table.items(), key=lambda kv: -kv[1][0]):
        w = sorted(waits)
        print(
            f"{key}: n={k} wait p50 {w[len(w)//2]:.1f} p90 {w[int(len(w)*.9)]:.1f} max {w[-1]:.1f}"
        )


if __name__ == "__main__":
    main()
