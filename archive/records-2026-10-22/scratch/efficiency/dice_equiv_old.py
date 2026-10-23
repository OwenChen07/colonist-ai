"""The previous encode._dice_features, for ab_shard.py's 'old' arm."""

from catanatron.models.dice import MIN_CARDS_BEFORE_RESHUFFLE


def old(dice, me, them):
    v = []
    v.append(float(dice.mode == "balanced"))
    v.append(dice.cards_left / 36)
    v.append(float(dice.cards_left < MIN_CARDS_BEFORE_RESHUFFLE))
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
