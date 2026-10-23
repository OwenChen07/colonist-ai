"""Where colonist's 139 entry sits relative to the robber move and the steal.

For every diff holding a robber move (type 11, pieceEnum 5) or a 139 entry:
the order of the kinds 11/139/steal in that diff, 139's playerCount, and
whether the bot (or the opponent) moved. Usage: robber139.py [N]
"""

import collections
import glob
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import game_start, DIFF

STEALS = {14, 15, 74}


def tag(t):
    k = t.get("type")
    if k == 11 and t.get("pieceEnum") == 5:
        return "R"
    if k == 139:
        return f"139({t.get('playerCount')})"
    if k in STEALS:
        return f"S{k}"
    return None


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    paths = []
    for acct in ("bot3", "bot4", "bot5", "bot7"):
        paths += glob.glob(f"captures/{acct}/bridge-*.json")
    paths.sort()
    random.Random(7).shuffle(paths)
    orders = collections.Counter()
    for p in paths[:n]:
        frames = load_capture(p)
        start = game_start(frames)
        if start is None:
            continue
        for f in frames:
            if f.dir != "in" or f.seq <= start.seq or f.type != DIFF:
                continue
            logs = f.data["data"]["payload"].get("diff", {}).get("gameLogState") or {}
            tags = [
                tag(v["text"])
                for _, v in sorted(logs.items(), key=lambda kv: int(kv[0]))
                if isinstance(v, dict) and isinstance(v.get("text"), dict)
            ]
            tags = [t for t in tags if t]
            if tags:
                orders[" ".join(tags)] += 1
    for key, k in orders.most_common():
        print(f"{k:6d}  {key}")


if __name__ == "__main__":
    main()
