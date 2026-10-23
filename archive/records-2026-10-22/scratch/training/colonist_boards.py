"""Do colonist's live boards keep 6s and 8s apart? Compare recorded boards with
our self-play generator (number_placement='random') and the official spiral.
Usage: colonist_boards.py [max_files]
"""

import glob
import json
import random
import sys
from collections import Counter

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.models.map import build_map, LandTile

base = build_map("BASE")
COORDS = [c for c, t in base.land_tiles.items()]


def dist(a, b):
    return max(abs(a[k] - b[k]) for k in range(3))


PAIRS = [
    (a, b) for i, a in enumerate(COORDS) for b in COORDS[i + 1 :] if dist(a, b) == 1
]
assert len(PAIRS) == 42, len(PAIRS)


def stats(num):  # coord -> number or None
    red = sum(1 for a, b in PAIRS if num.get(a) in (6, 8) and num.get(b) in (6, 8))
    same = sum(
        1 for a, b in PAIRS if num.get(a) is not None and num.get(a) == num.get(b)
    )
    return red, same


def summary(rows):
    n = len(rows)
    red = [r[0] for r in rows]
    same = [r[1] for r in rows]
    return (
        f"n={n}: 6/8 touching mean {sum(red)/n:.2f}, boards with any {sum(1 for x in red if x)/n:.0%}; "
        f"equal numbers touching mean {sum(same)/n:.2f}, boards with any {sum(1 for x in same if x)/n:.0%}"
    )


def find_map(o):
    if isinstance(o, dict):
        if "tileHexStates" in o:
            return o
        for v in o.values():
            r = find_map(v)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_map(v)
            if r is not None:
                return r
    return None


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
files = sorted(glob.glob("captures/*/bridge-*.json"))
random.Random(0).shuffle(files)
rows, seen, near, boards = [], set(), Counter(), []
for f in files:
    if len(rows) >= limit:
        break
    try:
        m = convert(load_capture(f)).record.get("map")
    except Exception:
        continue
    if not m:
        continue
    num = {
        tuple(t["coordinate"]): t["number"] for t in m["tiles"] if t["type"] == "LAND"
    }
    if len(num) != 19:
        continue
    key = tuple(sorted(num.items()))
    if key in seen:
        continue
    seen.add(key)
    rows.append(stats(num))
    boards.append(num)
    for a, b in PAIRS:
        if num.get(a) and num.get(b):
            near[tuple(sorted((num[a], num[b])))] += 1

print("colonist:", summary(rows))
sim = []
for s in range(5000):
    mp = build_map("BASE", "random")
    sim.append(stats({c: t.number for c, t in mp.land_tiles.items()}))
print("ours (random):", summary(sim))
spi = []
for s in range(500):
    mp = build_map("BASE", "official_spiral")
    spi.append(stats({c: t.number for c, t in mp.land_tiles.items()}))
print("official spiral:", summary(spi))
print(
    "colonist 6-8/6-6/8-8 touching pair counts:",
    near[(6, 8)],
    near[(6, 6)],
    near[(8, 8)],
)
print("colonist 2-12 / 5-9 pairs:", near[(2, 12)], near[(5, 9)])

import math

SPIRAL = [5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11]


def ring(r):
    cs = [c for c in COORDS if dist(c, (0, 0, 0)) == r]
    # angle in the plane: x - y/2 ... use axial projection
    return sorted(
        cs,
        key=lambda c: math.atan2(
            math.sqrt(3) * (c[1] - c[2]) / 2, c[0] - (c[1] + c[2]) / 2
        ),
    )


R2, R1 = ring(2), ring(1)
assert len(R2) == 12 and len(R1) == 6


def spirals():
    for d in (1, -1):
        for k in range(12):
            outer = [R2[(k + d * i) % 12] for i in range(12)]
            for m in range(6):
                inner = [R1[(m + d * i) % 6] for i in range(6)]
                yield outer + inner + [(0, 0, 0)]


def is_spiral(num):
    for path in spirals():
        if [num[c] for c in path if num[c] is not None] == SPIRAL:
            return True
    return False


def variant(num):
    for i, path in enumerate(spirals()):
        if [num[c] for c in path if num[c] is not None] == SPIRAL:
            return (path[0], path[1])
    return None


print(
    "colonist spiral start variants:", Counter(variant(b) for b in boards).most_common()
)
print(
    "ours spiral start variants:",
    Counter(
        variant(
            {
                c: t.number
                for c, t in build_map("BASE", "official_spiral").land_tiles.items()
            }
        )
        for _ in range(200)
    ).most_common(),
)
print(
    "our official_spiral boards matching SPIRAL:",
    sum(
        is_spiral(
            {
                c: t.number
                for c, t in build_map("BASE", "official_spiral").land_tiles.items()
            }
        )
        for _ in range(50)
    ),
    "/ 50",
)
print(
    "colonist boards matching SPIRAL:",
    sum(is_spiral(b) for b in boards),
    "/",
    len(boards),
)
print(
    "colonist desert at centre:",
    sum(1 for b in boards if b[(0, 0, 0)] is None),
    "/",
    len(boards),
)
print(
    "colonist distinct number layouts:",
    len({tuple(sorted(b.items(), key=str)) for b in boards}),
)
# which neighbour pairs never occur on colonist boards vs expected under random
exp = Counter()
for _ in range(2000):
    mp = build_map("BASE", "random")
    nm = {c: t.number for c, t in mp.land_tiles.items()}
    for a, b in PAIRS:
        if nm[a] and nm[b]:
            exp[tuple(sorted((nm[a], nm[b])))] += 1
scale = len(boards) / 2000
never = [
    (p, round(exp[p] * scale))
    for p in sorted(exp)
    if near[p] == 0 and exp[p] * scale >= 5
]
print("pairs never on colonist (expected count under random):", never)
