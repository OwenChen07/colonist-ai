"""Are OpenHex's dice plain random or balanced? Per game, the chi-square
dispersion of the 11 totals' counts against the 36-way odds. Plain dice give a
mean near 10 (10 degrees of freedom); balanced dice give much less."""

import glob
import json
import random
import sys

P = [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]


def rolls(obj, out):
    if isinstance(obj, dict):
        if obj.get("type") == "diceRolled" and "die1" in obj:
            out.append(obj["die1"] + obj["die2"])
        for v in obj.values():
            rolls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            rolls(v, out)
    return out


def chi2(totals):
    n = len(totals)
    return sum(
        (totals.count(t) - n * p / 36) ** 2 / (n * p / 36)
        for t, p in zip(range(2, 13), P)
    )


games = []
for path in sorted(glob.glob(sys.argv[1] + "/*.json")):
    with open(path) as f:
        r = rolls(json.load(f), [])
    if len(r) >= 20:
        games.append(r)
print(f"{len(games)} games, {sum(map(len, games))} rolls")
print(f"mean chi2 per game, observed: {sum(map(chi2, games)) / len(games):.2f}")
sim = [
    chi2([random.randint(1, 6) + random.randint(1, 6) for _ in range(len(g))])
    for g in games
    for _ in range(20)
]
print(f"mean chi2 per game, plain dice simulated: {sum(sim) / len(sim):.2f}")
allr = [t for g in games for t in g]
n = len(allr)
print("total   observed  expected")
for t, p in zip(range(2, 13), P):
    print(f"{t:5d} {allr.count(t):9d} {n * p / 36:9.0f}")
