"""Could OpenHex's dice come from a deck of the 36 ordered pairs (any
deck-based "balanced" scheme, colonist's included)? Within the first K rolls
of a game, K at most the draws before any reshuffle (colonist reshuffles
after 23 draws), a total can appear at most as often as the deck holds it:
2 and 12 once, 3 and 11 twice, ... 7 six times. Count games that break this.
Plain dice break it often; a deck never does."""

import glob
import json
import random
import sys

CARDS = dict(zip(range(2, 13), [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1]))


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


def breaks(seq):
    return any(seq.count(t) > c for t, c in CARDS.items())


games = []
for path in sorted(glob.glob(sys.argv[1] + "/*.json")):
    with open(path) as f:
        r = rolls(json.load(f), [])
    if len(r) >= 36:
        games.append(r)
for k in (23, 36):
    obs = sum(breaks(g[:k]) for g in games)
    sim = [
        breaks([random.randint(1, 6) + random.randint(1, 6) for _ in range(k)])
        for _ in range(20000)
    ]
    print(
        f"first {k} rolls: {obs} of {len(games)} games break the deck limit; "
        f"plain dice would break it in {100 * sum(sim) / len(sim):.0f}% of games; a deck in 0%"
    )
