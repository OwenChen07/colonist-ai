"""Audit item 3: how much of colonist's timer the bot uses, per state.

Usage: audit_botclock.py [N_CAPTURES]
For segments where it is the bot's turn (currentTurnPlayerColor == playerColor):
time left on colonist's timer at the segment start, how long the bot took,
and how often the segment ran to the deadline (the timer acted).
"""

import collections
import glob
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import game_start, DIFF


def q(xs, p):
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else float("nan")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    paths = sorted(glob.glob("captures/*/bridge-*.json"))
    random.Random(3).shuffle(paths)
    seg = collections.defaultdict(
        lambda: {"left": [], "dur": [], "frac": [], "late": 0, "ex": []}
    )
    used = 0
    for p in paths[:n]:
        frames = load_capture(p)
        start = game_start(frames)
        if start is None:
            continue
        payload = start.data["data"]["payload"]
        you = payload.get("playerColor")
        cur = dict(payload["gameState"].get("currentState", {}))
        used += 1
        t0 = start.t
        deadline = (
            t0 + 1000 * cur.get("allocatedTime", 0) if "startTime" in cur else None
        )
        key = (cur.get("turnState"), cur.get("actionState"))
        mine = cur.get("currentTurnPlayerColor") == you
        since = t0
        prev_key = None
        for f in frames:
            if f.dir != "in" or f.seq <= start.seq or f.type != DIFF or not f.t:
                continue
            d = f.data["data"]["payload"].get("diff", {}).get("currentState")
            if not isinstance(d, dict):
                continue
            new = dict(cur, **d)
            new_key = (new.get("turnState"), new.get("actionState"))
            new_turn = new.get("completedTurns") != cur.get("completedTurns")
            if new_key != key or new_turn:
                if mine and deadline is not None and key[0] in (0, 1, 2):
                    s = seg[key]
                    left = (deadline - since) / 1000
                    dur = (f.t - since) / 1000
                    s["left"].append(left)
                    s["dur"].append(dur)
                    s.setdefault("all_prev", collections.Counter())[prev_key] += 1
                    if left > 0:
                        s["frac"].append(dur / left)
                    if dur > left - 0.3:
                        s["late"] += 1
                        s.setdefault("days", collections.Counter())[
                            p.split("bridge-")[1][:8]
                        ] += 1
                        s.setdefault("prev", collections.Counter())[prev_key] += 1
                        if prev_key and prev_key[1] == 24:
                            s.setdefault("robex", []).append(f"{p}@{f.seq}")
                        if len(s["ex"]) < 3:
                            s["ex"].append(f"{p}@{f.seq}")
                since = f.t
                prev_key = key
                key = new_key
                mine = new.get("currentTurnPlayerColor") == you
            if "startTime" in d:
                deadline = f.t + 1000 * new.get("allocatedTime", 0)
            elif "allocatedTime" in d and deadline is not None:
                deadline += 1000 * (d["allocatedTime"] - cur.get("allocatedTime", 0))
            cur = new
    print(f"{used} games; bot's own segments by (turnState, actionState):")
    for key, s in sorted(seg.items(), key=lambda kv: -len(kv[1]["dur"])):
        L, D, F = sorted(s["left"]), sorted(s["dur"]), sorted(s["frac"])
        print(
            f"{key}: n={len(D)} left p50 {q(L,.5):.1f} | took p50 {q(D,.5):.2f} p90 {q(D,.9):.2f} "
            f"max {D[-1]:.1f} | share of time left p50 {q(F,.5):.2f} p90 {q(F,.9):.2f} "
            f"| ran out {s['late']} ({s['late']/len(D):.1%}) {s['ex']}"
        )
        if s.get("days"):
            print("    ran out by day:", sorted(s["days"].items()))
            print(
                "    ran out after (late/all):",
                [(k, v, s["all_prev"][k]) for k, v in s["prev"].most_common(6)],
            )
            print("    after a robber, examples:", s.get("robex", [])[-4:])
    days = collections.Counter(p.split("bridge-")[1][:8] for p in paths[:n])
    print("games by day:", sorted(days.items()))


if __name__ == "__main__":
    main()
