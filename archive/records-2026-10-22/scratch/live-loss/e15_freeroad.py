"""E15 check: free-road (Road Building) decision budgets per bridge restart segment.

Usage: e15_freeroad.py [bot ...]   (default bot3 bot4 bot5 bot7)
Splits data/<bot>/bridge.log at 'bridge listening' lines, dates each segment by
its 'game saved ... bridge-YYYYMMDD-HHMMSS.json' lines, and reports median ms and
sims of the free-road placements (the 'road on edge' lines right after 'play road
building'), split pre-roll vs in-turn.
"""

import re
import statistics
import sys

MOVE = re.compile(r"^\s+move: (.*) \(win chance \d+%, (\d+) ms(?:, (\d+) sims)?\)")
SAVED = re.compile(r"bridge-(\d{8}-\d{6})\.json")


def med(xs):
    return statistics.median(xs) if xs else float("nan")


def scan(bot):
    segs = []
    seg = None
    last_kind = None  # 'roll' or 'end' since the last game start
    pending = 0  # free roads still expected after a 'play road building'
    phase = None
    with open(f"/home/owen/colonist-ai/data/{bot}/bridge.log") as f:
        for line in f:
            if "bridge listening" in line or seg is None:
                seg = {"ts": [], "pre": [], "in": [], "plays": {"pre": 0, "in": 0}}
                segs.append(seg)
                if "bridge listening" in line:
                    continue
            m = SAVED.search(line)
            if m:
                seg["ts"].append(m.group(1))
                continue
            if "game started" in line:
                last_kind, pending = None, 0
                continue
            m = MOVE.match(line)
            if not m:
                continue
            move, ms, sims = m.group(1), int(m.group(2)), m.group(3)
            if pending and move.startswith("road on edge"):
                seg[phase].append((ms, int(sims) if sims else None))
                pending -= 1
                continue
            pending = 0
            if move == "play road building":
                phase = "in" if last_kind == "roll" else "pre"
                seg["plays"][phase] += 1
                pending = 2
            elif move == "roll":
                last_kind = "roll"
            elif move == "end turn":
                last_kind = "end"
    return segs


def summary(rows):
    ms = [r[0] for r in rows]
    sims = [r[1] for r in rows if r[1] is not None]
    return f"n={len(rows):3d} med_ms={med(ms):6.0f} med_sims={med(sims):6.0f} (sims field on {len(sims)})"


def pooled(args):
    """args like bot4=20261017-053601: pool Oct 16+ segments before/after that save."""
    for arg in args:
        bot, cut = arg.split("=")
        out = {"before": {"pre": [], "in": []}, "after": {"pre": [], "in": []}}
        games = {"before": 0, "after": 0}
        for s in scan(bot):
            if not s["ts"] or s["ts"][0] < "20261016-000000":
                continue
            side = "after" if s["ts"][0] >= cut else "before"
            games[side] += len(s["ts"]) // 2
            for k in ("pre", "in"):
                out[side][k] += s[k]
        print(f"== {bot} (cut {cut})")
        for side in ("before", "after"):
            print(f" {side:6s} ~{games[side]} games")
            print(f"   pre-roll roads: {summary(out[side]['pre'])}")
            print(f"   in-turn  roads: {summary(out[side]['in'])}")


def main():
    if any("=" in a for a in sys.argv[1:]):
        return pooled(sys.argv[1:])
    bots = sys.argv[1:] or ["bot3", "bot4", "bot5", "bot7"]
    for bot in bots:
        print(f"== {bot}")
        for i, s in enumerate(scan(bot)):
            if not (s["pre"] or s["in"]):
                continue
            ts = s["ts"]
            span = f"{ts[0]}..{ts[-1]} ({len(ts)} saves)" if ts else "no saves"
            print(
                f" seg {i:2d} {span} plays pre/in {s['plays']['pre']}/{s['plays']['in']}"
            )
            print(f"   pre-roll roads: {summary(s['pre'])}")
            print(f"   in-turn  roads: {summary(s['in'])}")


main()
