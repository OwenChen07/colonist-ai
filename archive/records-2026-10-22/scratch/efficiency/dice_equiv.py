"""Old vs new encode._dice_features on dice states from random-play games
(both seats, cold caches), then the time per call of each.

usage: dice_equiv.py [games]
"""

import random
import sys
import timeit

from catanatron.colonist import encode as E
from catanatron.colonist.selfplay import fresh_game


def old(dice, me, them):
    v = []
    v.append(float(dice.mode == "balanced"))
    v.append(dice.cards_left / 36)
    v.append(float(dice.cards_left < E.MIN_CARDS_BEFORE_RESHUFFLE))
    v += [len(p) / 6 for p in dice.deck]
    recent = [0.0] * 55
    for slot, total in enumerate(reversed(dice.recent)):
        recent[slot * 11 + total - 2] = 1.0
    v += recent
    v.append(dice.sevens.get(me, 0) / 5)
    v.append(dice.sevens.get(them, 0) / 5)
    v.append(float(dice.streak_color == me))
    v.append(float(dice.streak_color == them))
    v.append(dice.streak_count / 3)
    v += [x * 6 for x in dice.odds(me)]
    v += [x * 6 for x in dice.odds(them)]
    return v


games = int(sys.argv[1]) if len(sys.argv) > 1 else 40
cases = []
for s in range(games):
    g = fresh_game(6000 + s)
    rng = random.Random(s)
    step = 0
    while g.winning_color() is None and step < 1500:
        g.execute(rng.choice(g.playable_actions))
        step += 1
        if step % 5 == 0:
            d = g.state.dice.copy()
            if s % 10 == 9:  # some dice gone plain random
                d.mode = "random"
            a, b = g.state.colors
            cases.append((d, a, b))
            cases.append((d, b, a))
modes = {}
differ = 0
for d, me, them in cases:
    d1, d2 = d.copy(), d.copy()
    d1._cache.clear()
    d2._cache.clear()
    x, y = old(d1, me, them), E._dice_features(d2, me, them)
    # bit-identical, and the same types (ints vs floats)
    if repr(x) != repr(y):
        differ += 1
    # later odds/weights from the filled cache match the old ones
    for c in (me, them):
        if repr(d1.odds(c)) != repr(d2.odds(c)) or repr(d1.weights(c)) != repr(
            d2.weights(c)
        ):
            differ += 1
    key = (d.mode, d.cards_left < E.MIN_CARDS_BEFORE_RESHUFFLE, d.streak_count > 0)
    modes[key] = modes.get(key, 0) + 1
print(
    f"{len(cases)} checks, {differ} differ; cases by (mode, low deck, streak): {modes}"
)

cold = [(d.copy(), me, them) for d, me, them in cases]


def run(fn):
    for d, me, them in cold:
        d._cache.clear()
        fn(d, me, them)


def clear_only():
    for d, me, them in cold:
        d._cache.clear()


base = min(timeit.repeat(clear_only, number=5, repeat=3))
for name, fn in (("old", old), ("new", E._dice_features)):
    t = min(timeit.repeat(lambda: run(fn), number=5, repeat=3)) - base
    print(f"{name}: {t / 5 / len(cold) * 1e6:.2f} us per call")
