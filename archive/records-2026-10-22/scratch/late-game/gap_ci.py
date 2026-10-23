"""Stated minus won (points) at bot turn k, by opponent band, bootstrap 95% by game.
Reads peak_story.json."""

import json
import random

G = json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
rng = random.Random(7)


def ci(xs):
    m = sum(xs) / len(xs)
    bs = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(2000))
    return f"{m:+.1f} [{bs[50]:+.1f}, {bs[-51]:+.1f}] (n {len(xs)})"


for name, lo, hi in (
    ("<2100", 0, 2100),
    ("2100-2300", 2100, 2300),
    ("2300+", 2300, 9999),
    ("2400+", 2400, 9999),
):
    S = [g for g in G if g["mmr"] and lo <= g["mmr"] < hi]
    parts = [
        f"setup {ci([g['open_p'] - 100 * g['won'] for g in S if g['open_p'] is not None])}"
    ]
    for k in ("10", "15", "20", "25"):
        xs = [
            g["at_turn"][k] - 100 * g["won"]
            for g in S
            if g["at_turn"][k] is not None and 10 <= g["at_turn"][k] <= 90
        ]
        parts.append(f"k={k} (stated 10-90) {ci(xs)}")
    print(name + ":\n  " + "\n  ".join(parts))
    for T in ("70", "80"):
        X = [g for g in S if g["first"][T] is not None]
        print(
            f"  crossed {T}: won short of {T} by {ci([int(T) - 100 * g['won'] for g in X])}"
        )
