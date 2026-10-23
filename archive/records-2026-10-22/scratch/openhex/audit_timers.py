"""Audit item 3: colonist's timers per (turnState, actionState), from captures.

Usage: audit_timers.py [N_CAPTURES]
Merges currentState across the snapshot and diffs. For each change of
(turnState, actionState) reports: how often a new startTime came with it,
the allocatedTime values, and how long the state lasted (wall clock).
"""

import collections
import glob
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import game_start, DIFF, GAME_STATE


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    paths = sorted(glob.glob("captures/*/bridge-*.json"))
    random.Random(2).shuffle(paths)
    stats = collections.defaultdict(
        lambda: {"n": 0, "new_start": 0, "alloc": collections.Counter(), "dur": []}
    )
    alloc_growth = collections.Counter()
    for p in paths[:n]:
        frames = load_capture(p)
        start = game_start(frames)
        if start is None:
            continue
        cur = dict(start.data["data"]["payload"]["gameState"].get("currentState", {}))
        key = (cur.get("turnState"), cur.get("actionState"))
        since = start.t
        s = stats[key]
        s["n"] += 1
        s["new_start"] += "startTime" in cur
        s["alloc"][cur.get("allocatedTime")] += 1
        for f in frames:
            if f.dir != "in" or f.seq <= start.seq or f.type != DIFF:
                continue
            d = f.data["data"]["payload"].get("diff", {}).get("currentState")
            if not isinstance(d, dict):
                continue
            prev_key = key
            cur.update(d)
            key = (cur.get("turnState"), cur.get("actionState"))
            if key == prev_key:
                if "allocatedTime" in d and "startTime" not in d:
                    alloc_growth[(key, d["allocatedTime"])] += 1
                continue
            if since and f.t:
                stats[prev_key]["dur"].append((f.t - since) / 1000)
            since = f.t
            s = stats[key]
            s["n"] += 1
            s["new_start"] += "startTime" in d
            s["alloc"][d.get("allocatedTime", "same")] += 1
            s.setdefault("from", collections.Counter())[prev_key] += 1
    print(f"{n} captures")
    for key, s in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
        dur = sorted(s["dur"])
        q = (
            f"dur p50 {dur[len(dur)//2]:.1f} p95 {dur[int(len(dur)*.95)]:.1f} max {dur[-1]:.1f}"
            if dur
            else ""
        )
        frm = s.get("from", collections.Counter()).most_common(3)
        print(
            f"{key}: n={s['n']} new_start={s['new_start']/s['n']:.2f} "
            f"alloc={dict(s['alloc'].most_common(5))} {q} from={frm}"
        )
    print("allocatedTime changes without a new start:", alloc_growth.most_common(12))


if __name__ == "__main__":
    main()
