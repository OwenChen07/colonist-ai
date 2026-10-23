"""First timers of a game: per capture, the first frames whose currentState has
startTime/allocatedTime (frame arrival t, lag t-startTime, allocated, the turn's
player and state). Usage: setup_timer.py CAPTURE..."""

import sys

from catanatron.colonist.capture import load


def states(x, path=()):
    if isinstance(x, dict):
        cur = x.get("currentState")
        if isinstance(cur, dict) and ("startTime" in cur or "allocatedTime" in cur):
            yield cur
        for v in x.values():
            yield from states(v)
    elif isinstance(x, list):
        for v in x:
            yield from states(v)


for path in sys.argv[1:]:
    frames = load(path)
    print("==", path.rsplit("/", 1)[-1])
    shown = 0
    t0 = None
    for f in frames:
        if f.dir in ("open", "close") and t0 is not None:
            print(f"  +{(f.t - t0) / 1000:6.1f}s socket {f.socket} {f.dir}")
        if (
            f.dir == "out"
            and t0 is not None
            and f.t - t0 < 70000
            and isinstance(f.data, dict)
            and f.data.get("header") == [3, 1]
        ):
            print(f"  +{(f.t - t0) / 1000:6.1f}s OUT {str(f.data)[:90]}")
        if f.dir != "in":
            continue
        for cur in states(f.data):
            st = cur.get("startTime")
            if t0 is None:
                t0 = f.t
            lag = (f.t - st) / 1000 if isinstance(st, (int, float)) else None
            print(
                f"  +{(f.t - t0) / 1000:6.1f}s lag={lag} alloc={cur.get('allocatedTime')}"
                f" turns={cur.get('completedTurns')} player={cur.get('currentTurnPlayerColor')}"
                f" state={cur.get('turnState')}/{cur.get('actionState')}"
            )
            shown += 1
        if shown >= 5:
            break
