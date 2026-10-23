"""Are OpenHex's dice colonist's balanced dice? Feed every saved game's rolls
through our BalancedDice.observe (it switches to 'random' at the first roll
colonist's dice could not have made), and compare per-game spread of totals
with simulated balanced and plain dice of the same lengths.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/dice.py
"""

import glob
import json
import random
import statistics as st

from catanatron.models.dice import BalancedDice, roll_plain_dice

EXP = [min(t - 1, 13 - t) / 36 for t in range(2, 13)]


def chi2(totals):
    n = len(totals)
    c = [0] * 11
    for t in totals:
        c[t - 2] += 1
    return sum((c[i] - n * EXP[i]) ** 2 / (n * EXP[i]) for i in range(11))


def max_run_same_pair(rolls):
    """Most times one ordered pair came up within any 24 consecutive rolls."""
    best = 0
    for i in range(len(rolls)):
        w = rolls[i : i + 24]
        best = max(best, max(w.count(p) for p in w))
    return best


games = []
for path in sorted(glob.glob("data/openhex/games/*.json")):
    g = json.load(open(path))
    rolls = []
    for e in sorted(g.get("events", []), key=lambda e: e["seq"]):
        ev = e["event"]
        if ev["type"] == "diceRolled":
            rolls.append((ev.get("player"), (int(ev["die1"]), int(ev["die2"]))))
    if len(rolls) >= 10:
        games.append((path.split("/")[-1][:8], rolls))

print(f"{len(games)} games, {sum(len(r) for _, r in games)} rolls")
broke = []
for gid, rolls in games:
    d = BalancedDice(2)
    at = None
    for k, (p, pair) in enumerate(rolls):
        d.observe(p, pair)
        if d.mode != "balanced" and at is None:
            at = k
    broke.append(at)
nb = [b for b in broke if b is not None]
print(
    f"impossible under colonist balanced dice: {len(nb)}/{len(games)} games;"
    f" first impossible roll at index median {st.median(nb) if nb else '-'}"
)

real = [chi2([a + b for _, (a, b) in r]) for _, r in games]
rng = random.Random(1)
sim_plain, sim_bal, run_plain, run_bal = [], [], [], []
for _ in range(20):
    for _, r in games:
        n = len(r)
        pl = [roll_plain_dice(rng) for _ in range(n)]
        d = BalancedDice(2)
        bl = [d.roll(rng, ("RED", "BLUE")[k % 2]) for k in range(n)]
        sim_plain.append(chi2([a + b for a, b in pl]))
        sim_bal.append(chi2([a + b for a, b in bl]))
        run_plain.append(max_run_same_pair(pl))
        run_bal.append(max_run_same_pair(bl))
run_real = [max_run_same_pair([p for _, p in r]) for _, r in games]
print("per-game chi-square of totals vs 2d6 (df 10; lower = more even):")
print(f"  OpenHex       mean {st.mean(real):.2f}  median {st.median(real):.2f}")
print(
    f"  sim plain     mean {st.mean(sim_plain):.2f}  median {st.median(sim_plain):.2f}"
)
print(f"  sim balanced  mean {st.mean(sim_bal):.2f}  median {st.median(sim_bal):.2f}")
print("max repeats of one ordered pair within 24 rolls:")
print(
    f"  OpenHex {st.mean(run_real):.2f}  plain {st.mean(run_plain):.2f}  balanced {st.mean(run_bal):.2f}"
)
allt = [a + b for _, r in games for _, (a, b) in r]
n = len(allt)
print("pooled totals, observed / expected:")
print("  " + " ".join(f"{t}:{allt.count(t)}/{n*EXP[t-2]:.0f}" for t in range(2, 13)))
