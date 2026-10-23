"""Compare OpenHex board layouts with our self-play generator (random numbers).

Stats per board: adjacent 6/8 pairs, adjacent equal-number pairs, desert in
the centre, port layout (edges) fixed or not. Usage: boards.py
"""

import glob
import json
import random
from collections import Counter

from catanatron.openhex.board import HEXES as HEX

assert len(set(HEX)) == 19


def adj_pairs():
    out = []
    for i, a in enumerate(HEX):
        for j, b in enumerate(HEX):
            if i < j and sum(abs(a[k] - b[k]) for k in range(3)) == 2:
                out.append((i, j))
    return out


PAIRS = adj_pairs()
assert len(PAIRS) == 42, len(PAIRS)
TERR = (
    ["forest"] * 4
    + ["pasture"] * 4
    + ["fields"] * 4
    + ["hills"] * 3
    + ["mountains"] * 3
    + ["desert"]
)
NUMS = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]


def stats(num):  # num: hex index -> number or None
    red = sum(1 for i, j in PAIRS if num[i] in (6, 8) and num[j] in (6, 8))
    same = sum(1 for i, j in PAIRS if num[i] is not None and num[i] == num[j])
    desert_c = num[0] is None
    return red, same, desert_c


def sim(n=20000):
    rng = random.Random(1)
    out = []
    for _ in range(n):
        t = rng.sample(TERR, 19)
        ns = rng.sample(NUMS, 18)
        num = [None if x == "desert" else ns.pop() for x in t]
        out.append(stats(num))
    return out


def summary(rows):
    n = len(rows)
    red = [r[0] for r in rows]
    same = [r[1] for r in rows]
    return (
        f"n={n} red-adjacent mean {sum(red)/n:.2f}, share with any {sum(1 for x in red if x)/n:.0%}; "
        f"equal-adjacent mean {sum(same)/n:.2f}, share with any {sum(1 for x in same if x)/n:.0%}; "
        f"desert centre {sum(1 for r in rows if r[2])/n:.0%}"
    )


boards = {}
for f in glob.glob("data/openhex/games/*.json"):
    txt = open(f).read()
    try:
        d = json.loads(txt)
    except Exception:
        continue
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "tiles" in o and isinstance(o["tiles"], list) and len(o["tiles"]) == 19:
                found.append(o)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(d)
    if found:
        boards[f] = found[0]

rows, ports, terr_pos, num_seq = [], Counter(), Counter(), Counter()
for f, b in boards.items():
    num = [None] * 19
    for t in b["tiles"]:
        num[t["hex"]] = t.get("number") if t["terrain"] != "desert" else None
    rows.append(stats(num))
    ports[json.dumps(sorted(json.dumps(p["edge"]) for p in b.get("ports", [])))] += 1
    num_seq[tuple(num)] += 1
print("OpenHex:", summary(rows))
print("ours   :", summary(sim()))
print(
    "distinct port edge layouts:",
    len(ports),
    "over",
    len(boards),
    "boards; top count",
    ports.most_common(1)[0][1] if ports else 0,
)
print("distinct number layouts:", len(num_seq))
pres = Counter(
    json.dumps([p.get("resource") for p in b.get("ports", [])]) for b in boards.values()
)
terr = Counter(
    json.dumps(sorted((t["hex"], t["terrain"]) for t in b["tiles"]))
    for b in boards.values()
)
print(
    "distinct port resource orders:",
    len(pres),
    "; distinct terrain layouts:",
    len(terr),
)
for k, c in num_seq.items():
    if c > 1:
        print("repeated number layout x", c)
# 2/12 and 6/8 next to 2/12? check whether any pair of {6,8} with {5,9} etc is also avoided
near = Counter()
for b in boards.values():
    num = {t["hex"]: t.get("number") for t in b["tiles"] if t["terrain"] != "desert"}
    for i, j in PAIRS:
        if i in num and j in num:
            near[tuple(sorted((num[i], num[j])))] += 1
print(
    "adjacent number pairs never seen:",
    [
        p
        for p in [(a, b) for a in range(2, 13) for b in range(a, 13) if 7 not in (a, b)]
        if near[p] == 0
    ],
)
SPIRAL = [5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11]
R1, R2 = HEX[1:7], HEX[7:19]
idx = {h: i for i, h in enumerate(HEX)}


def spirals():
    for d in (1, -1):
        for k in range(12):
            outer = [R2[(k + d * i) % 12] for i in range(12)]
            for m in range(6):
                inner = [R1[(m + d * i) % 6] for i in range(6)]
                yield k, d, m, outer + inner + [HEX[0]]


matched, starts = 0, Counter()
for b in boards.values():
    num = {
        idx[HEX[t["hex"]]]: (None if t["terrain"] == "desert" else t.get("number"))
        for t in b["tiles"]
    }
    ok = None
    for k, d, m, path in spirals():
        seq = [num[idx[h]] for h in path if num[idx[h]] is not None]
        if seq == SPIRAL:
            ok = (k, d, m)
            break
    if ok:
        matched += 1
        starts[ok] += 1
print(
    f"boards matching the official number spiral: {matched}/{len(boards)}; start variants used {dict(starts)}"
)
print(
    "port rates/resources sample:",
    [
        (p.get("rate"), p.get("resource"))
        for p in next(iter(boards.values())).get("ports", [])
    ],
)
