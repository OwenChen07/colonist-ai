"""Per round: self-play wall time (its prefetch.log) vs gate wall time
(train.log), whether they overlap, and the gate's share of fleet time."""

import re
import sys

first = int(sys.argv[1]) if len(sys.argv) > 1 else 145
ts = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]")


def sec(line):
    m = ts.match(line)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def tod(t):
    return t % 86400


gate = {}  # round -> [start, end, games]
cur = None
for line in open("runs/train.log"):
    m = re.search(r"round (\d+): promotion match", line)
    if m:
        cur = int(m[1])
        gate[cur] = [sec(line), None, 0]
    m = re.search(r"round (\d+): live value MSE", line)
    if m and int(m[1]) in gate:
        gate[int(m[1])][1] = sec(line)
    m = re.search(r"\((\d+) wins, (\d+) losses, (\d+) no winner; (\d+) games", line)
    if m and cur in gate:
        gate[cur][2] = int(m[4])

rows = []
for r in sorted(gate):
    if r < first or gate[r][1] is None:
        continue
    sp = None
    try:
        lines = open(f"runs/g1/round_{r + 1}/prefetch.log").read().splitlines()
        s = next(
            sec(l) for l in lines if "self-play by" in l or "games, opponents" in l
        )
        e = next(sec(l) for l in lines if "self-play complete across" in l)
        sp = (s, e)
    except (OSError, StopIteration):
        pass
    g0, g1, n = gate[r]
    gw = (g1 - g0) % 86400
    if sp:
        sw = (sp[1] - sp[0]) % 86400
        overlap = (sp[1] - g0) % 86400 < 43200 and (sp[1] - g0) % 86400 > 0
        rows.append((r, sw, gw, n, overlap))
print("round  selfplay_min  gate_min  gate_games  overlap  gate_share")
for r, sw, gw, n, ov in rows:
    print(f"{r:5d} {sw/60:12.1f} {gw/60:9.1f} {n:10d} {str(ov):>8} {gw/(sw+gw):10.2f}")
if rows:
    S = sum(x[1] for x in rows)
    G = sum(x[2] for x in rows)
    N = sum(x[3] for x in rows)
    print(
        f"total: self-play {S/60:.0f} min for {5000*len(rows)} games, gate {G/60:.0f} min for {N} games"
    )
    print(
        f"fleet s per game: self-play {S/(5000*len(rows)):.3f}, gate {G/N:.3f}; gate share {G/(S+G):.2f}"
    )
