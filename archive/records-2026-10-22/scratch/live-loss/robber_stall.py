"""How often the bot stalls after its own robber move with nobody to rob,
before and after a cutoff (the 139 fix's deploy). Usage:
robber_stall.py CUTOFF_UTC [STALL_S]   e.g. robber_stall.py 20261019-200000 15

CUTOFF compares with the capture name (bridge-YYYYMMDD-HHMMSS, UTC).
For each bot robber move (log 11, pieceEnum 5) with no steal after it: the
seconds until the next other log entry. A wait over STALL_S counts as a stall.
Ranked accounts bot3/4/5/7 only (never bot8).
"""

import glob
import os
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import game_start, DIFF

ACCOUNTS = ["bot3", "bot4", "bot5", "bot7"]
STEALS = {14, 15, 74}


def waits(path):
    frames = load_capture(path)
    start = game_start(frames)
    if start is None:
        return None
    you = start.data["data"]["payload"].get("playerColor")
    out = []  # (stolen, wait_s)
    pending = None
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
                pending = f.t if t.get("playerColor") == you else None
            elif pending is not None and kind in STEALS:
                pending = None
            elif pending is not None and kind not in (139, 11):
                out.append((f.t - pending) / 1000)
                pending = None
    return out


def main():
    cutoff = sys.argv[1]
    stall = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    rows = {
        "before": [0, 0, 0, 0],
        "after": [0, 0, 0, 0],
    }  # games, moves, stalls, games w/ stall
    for acct in ACCOUNTS:
        for p in sorted(glob.glob(f"captures/{acct}/bridge-*.json")):
            stamp = os.path.basename(p)[len("bridge-") : -len(".json")]
            side = "after" if stamp >= cutoff else "before"
            try:
                w = waits(p)
            except Exception:
                continue
            if w is None:
                continue
            r = rows[side]
            r[0] += 1
            r[1] += len(w)
            s = sum(x > stall for x in w)
            r[2] += s
            r[3] += s > 0
    for side, (g, m, s, gs) in rows.items():
        if g:
            print(
                f"{side}: {g} games, {m} bot robber moves with nobody robbed, "
                f"{s} stalls >{stall:.0f}s ({100*s/max(m,1):.1f}%), "
                f"{gs} games with a stall ({100*gs/g:.1f}%)"
            )


if __name__ == "__main__":
    main()
