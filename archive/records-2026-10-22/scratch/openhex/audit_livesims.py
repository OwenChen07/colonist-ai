"""Audit item 5: simulations per searched live move, by move kind, from the
bridge logs' "move: X (win chance P%, T ms, N sims)" lines. Only lines after
the last "net ... round R" header with R >= MIN_ROUND (default 60).
Usage: audit_livesims.py [MIN_ROUND]"""

import collections
import glob
import re
import statistics as st
import sys

MIN_ROUND = int(sys.argv[1]) if len(sys.argv) > 1 else 60
MOVE = re.compile(r"move: (.+?) \(win chance (\d+)%, (\d+) ms, (\d+) sims\)")
NET = re.compile(r"\(g1 round (\d+)\), (\d+) simulations")


def kind(text):
    for k in (
        "settlement",
        "road",
        "city",
        "robber",
        "discard",
        "development",
        "end turn",
        "roll",
        "knight",
        "monopoly",
        "year of plenty",
        "road building",
        "trade",
    ):
        if k in text:
            return k
    return text.split()[0]


sims = collections.defaultdict(list)
setup = collections.defaultdict(list)
under = collections.Counter()
for path in sorted(glob.glob("/home/owen/colonist-ai/data/bot*/bridge.log")):
    ok = False
    turn = 0
    for line in open(path, errors="replace"):
        m = NET.search(line)
        if m:
            ok = int(m.group(1)) >= MIN_ROUND
            turn = 0
            continue
        if "game started" in line:
            turn = 0
        m = MOVE.search(line)
        if not m or not ok:
            continue
        n = int(m.group(4))
        if n == 0:
            continue
        k = kind(m.group(1))
        turn += 1
        if turn <= 4 and k in ("settlement", "road"):
            setup[k].append(n)
        else:
            sims[k].append(n)
            under[k] += n < 800


def show(name, xs):
    q = st.quantiles(xs, n=10) if len(xs) > 9 else [0] * 9
    print(
        f"{name:22s} n={len(xs):7d} median {st.median(xs):6.0f} "
        f"p10 {q[0]:6.0f} p90 {q[-1]:6.0f}",
        end="",
    )


for k, xs in sorted(setup.items()):
    show("setup " + k, xs)
    print()
total = []
for k, xs in sorted(sims.items(), key=lambda kv: -len(kv[1])):
    show(k, xs)
    print(f" under 800: {under[k] / len(xs):.1%}")
    total += xs
show("all non-setup", total)
print(
    f" under 800: {sum(x < 800 for x in total) / len(total):.1%}, "
    f"under 200: {sum(x < 200 for x in total) / len(total):.1%}"
)
