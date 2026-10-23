"""E72 follow-up: first-seat win rate in self-play matches, from match lines.

Pools 'first seat won X%' over match lines in the training logs (gate matches
of rounds >= FROM) and in research's E80 log (round 182 at 2,400 sims).

    .venv/bin/python data/orchestrator/scratch/live-loss/seat_selfplay.py [FROM]
"""

import math
import re
import sys
from pathlib import Path

FROM = int(sys.argv[1]) if len(sys.argv) > 1 else 180
LINE = re.compile(
    r"(Z:\S+|AB:\d+) vs (Z:\S+|AB:\d+|\S+): .*?(\d+) games, \d+ s; first seat won ([\d.]+)%"
)


def pool(label, items):
    n = sum(k for k, _ in items)
    w = sum(k * p / 100 for k, p in items)
    if not n:
        print(f"{label}: none")
        return
    p = w / n
    h = 1.96 * math.sqrt(p * (1 - p) / n)
    print(
        f"{label}: first seat {100*p:.1f}% ± {100*h:.1f} over {n} games ({len(items)} matches)"
    )


seen = set()
gate, people, other = [], [], []
for f in [Path("runs/train.log"), Path("runs/g1/train-tail.log")]:
    if not f.exists():
        continue
    for line in open(f, errors="replace"):
        m = LINE.search(line)
        if not m or line.lstrip().startswith("[") is False and "vs" not in line:
            continue
        key = m.group(0)
        if key in seen:
            continue
        seen.add(key)
        r = re.search(r"round_(\d+)\.npz", m.group(1))
        if not r or int(r.group(1)) < FROM:
            continue
        item = (int(m.group(3)), float(m.group(4)))
        if "round_" in m.group(2) and ":200" in m.group(1):
            gate.append(item)
        elif "people" in m.group(2):
            people.append(item)
        else:
            other.append((item, m.group(1)[-20:], m.group(2)[-30:]))
pool(f"gate matches, rounds {FROM}+, 200 sims, new vs best", gate)
pool(f"people-model checks, rounds {FROM}+", people)
for item, a, b in other[-8:]:
    print(f"  other: {a} vs {b}: {item[0]} games, first seat {item[1]}%")

# E80: the last cumulative line of each seed batch in research's results
e80 = Path("data/orchestrator/e80/results.txt")
if e80.exists():
    last = {}
    batch = None
    for line in open(e80, errors="replace"):
        s = re.search(r"seed (\d+)", line)
        if s:
            batch = s.group(1)
            continue
        m = LINE.search(line)
        if m and batch and ":2400" in m.group(1):
            last[batch] = (int(m.group(3)), float(m.group(4)))
    pool(
        "E80 round 182 vs itself, 2,400 sims (20k vs 11k opening)", list(last.values())
    )
    for k, v in last.items():
        print(f"  seed {k}: {v[0]} games, first seat {v[1]}%")
