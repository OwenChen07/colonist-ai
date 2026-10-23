"""Colonist's ports and resource tiles vs ours: port positions/types, resource
adjacency (e.g. same resource touching), desert position.
Usage: colonist_ports.py [max_files]
"""

import glob
import random
import sys
from collections import Counter

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.models.map import build_map

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
files = sorted(glob.glob("captures/*/bridge-*.json"))
random.Random(0).shuffle(files)

first = None
boards, seen = [], set()
for f in files:
    if len(boards) >= limit:
        break
    try:
        m = convert(load_capture(f)).record.get("map")
    except Exception:
        continue
    if not m:
        continue
    if first is None:
        first = m
        types = Counter(t["type"] for t in m["tiles"])
        print("tile types:", types)
        print(
            "sample non-land tiles:", [t for t in m["tiles"] if t["type"] != "LAND"][:3]
        )
        print("sample land tile:", [t for t in m["tiles"] if t["type"] == "LAND"][:1])
    key = str(sorted((str(t) for t in m["tiles"])))
    if key in seen:
        continue
    seen.add(key)
    boards.append(m)

print("boards:", len(boards))


def ports_of(tiles):
    return tuple(
        sorted(
            (tuple(t["coordinate"]), t.get("direction"), t.get("resource"))
            for t in tiles
            if t["type"] == "PORT"
        )
    )


def land_of(tiles):
    return {
        tuple(t["coordinate"]): t.get("resource") for t in tiles if t["type"] == "LAND"
    }


pos = Counter(tuple((p[0], p[1]) for p in ports_of(b["tiles"])) for b in boards)
print("colonist distinct port position sets:", len(pos), pos.most_common(3))
ptypes = Counter(tuple(p[2] for p in ports_of(b["tiles"])) for b in boards)
print("colonist distinct port type orders:", len(ptypes))


def dist(a, b):
    return max(abs(a[k] - b[k]) for k in range(3))


def same_res_pairs(land):
    cs = list(land)
    return sum(
        1
        for i, a in enumerate(cs)
        for b in cs[i + 1 :]
        if dist(a, b) == 1 and land[a] is not None and land[a] == land[b]
    )


col = [same_res_pairs(land_of(b["tiles"])) for b in boards]
print("colonist same-resource touching pairs mean:", sum(col) / len(col))
print(
    "colonist desert at centre:",
    sum(1 for b in boards if land_of(b["tiles"]).get((0, 0, 0)) is None),
    "/",
    len(boards),
)

ours_pos = Counter()
ours_same = []
ours_desert_c = 0
for _ in range(500):
    mp = build_map("BASE", "random")
    ports = tuple(
        sorted(
            (c, str(t.direction))
            for c, t in mp.tiles.items()
            if type(t).__name__ == "Port"
        )
    )
    ours_pos[ports] += 1
    land = {c: t.resource for c, t in mp.land_tiles.items()}
    ours_same.append(same_res_pairs(land))
    ours_desert_c += land[(0, 0, 0)] is None
print("ours distinct port position sets:", len(ours_pos), list(ours_pos)[0])
orders = Counter(
    tuple(
        str(t.resource)
        for c, t in sorted(build_map("BASE", "random").tiles.items())
        if type(t).__name__ == "Port"
    )
    for _ in range(500)
)
print("ours distinct port type orders in 500:", len(orders))
print("ours same-resource touching pairs mean:", sum(ours_same) / len(ours_same))
print("ours desert at centre:", ours_desert_c, "/ 500")
