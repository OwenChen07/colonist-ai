"""colonist.io's balanced dice.

colonist's "balanced dice" draw each roll from a deck of the 36 ordered pairs
of dice faces, then nudge the odds. This is a port of colonist's
DiceControllerBalanced, by way of the line-for-line JavaScript port kept as a
test reference in the colonist-tracker extension (test/colonistDice.js):

  - Deck: 36 cards, one per ordered pair. A drawn card is removed. Before a
    draw, if fewer than 13 cards remain, all 36 go back in.
  - Base weight of a total = cards of that total left / cards left.
  - Recency: weight x (1 - 0.34 x times that total came up in the last 5
    rolls), floored at 0.
  - 7s, depending on who is rolling:
        share  = 1 + (ideal - mine) / ideal, where ideal = 1 / players who
                 have rolled and mine = their share of all 7s (just 1 until
                 there have been at least that many 7s)
        streak = 0.4 x length of the current run of 7s by one player,
                 negative if the run is the roller's, positive otherwise.
                 A run only ends when another player rolls a 7.
        7 weight x clamp(share + streak, 0, 2)

The state is everything the game log shows (every roll and who rolled it), so
an observer can follow it exactly: observe() applies a roll that happened
elsewhere, roll() makes one. If an observed roll is impossible under these
rules, the dice that produced it were not balanced, and the model switches to
plain random dice from then on, as the tracker does.
"""

import random
from collections import deque

TOTALS = tuple(range(2, 13))
SEVEN = 7 - 2  # index of total 7
DECK_SIZE = 36
MIN_CARDS_BEFORE_RESHUFFLE = 13
RECENT_REDUCTION = 0.34
RECENT_MEMORY = 5
SEVEN_STREAK_REDUCTION = 0.4


_FULL_DECK = tuple(
    tuple((a, t - a) for a in range(1, 7) if 1 <= t - a <= 6) for t in TOTALS
)
_FULL_COUNTS = tuple(len(p) for p in _FULL_DECK)


def full_deck():
    """For each total 2..12, its ordered dice pairs in colonist's order."""
    return [list(pairs) for pairs in _FULL_DECK]


def roll_plain_dice(rng=None):
    rng = rng if rng is not None else random
    return (rng.randint(1, 6), rng.randint(1, 6))


class BalancedDice:
    def __init__(self, num_players=2):
        self.num_players = num_players
        # Each total's pairs are a tuple, replaced (never changed in place) on
        # a draw, so copy() shares them: copying is the search's inner loop.
        self.deck = list(_FULL_DECK)
        self._cache = {}  # weights and odds by color, until the state changes
        self.cards_left = DECK_SIZE
        self.recent = deque()  # last <= 5 totals
        self.recent_count = [0] * 11
        self.sevens = {}  # color -> 7s rolled; a color appears once it has rolled
        self.streak_color = None
        self.streak_count = 0
        self.mode = "balanced"  # "balanced" | "random"

    # ----- odds -----
    def weights(self, color):
        """Unnormalized weight of each total if `color` rolled now, exactly as
        colonist computes them just before a draw. Cached until the dice
        change: search asks for the same odds many times."""
        key = ("w", color)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._cache[key] = self._weights(color)
        return list(cached)

    def _weights(self, color):
        if self.cards_left < MIN_CARDS_BEFORE_RESHUFFLE:
            counts, left = _FULL_COUNTS, DECK_SIZE
        else:
            counts, left = [len(p) for p in self.deck], self.cards_left
        w = []
        for i in range(11):
            x = counts[i] / left
            x *= 1 - self.recent_count[i] * RECENT_REDUCTION
            w.append(x if x > 0 else 0)
        if self.num_players >= 2:
            w[SEVEN] *= self.seven_multiplier(color)
        return w

    def odds(self, color):
        """Probability of each total 2..12 if `color` rolled now."""
        if self.mode != "balanced":
            return [min(t - 1, 13 - t) / 36 for t in TOTALS]
        key = ("o", color)
        cached = self._cache.get(key)
        if cached is None:
            w = self.weights(color)
            s = sum(w)
            cached = self._cache[key] = [x / s for x in w]
        return list(cached)

    def seven_multiplier(self, color):
        """The factor on the 7 weight if `color` rolled now. The roller counts
        as having rolled before the draw."""
        seen = dict(self.sevens)
        seen.setdefault(color, 0)
        total = sum(seen.values())
        if total < len(seen):
            share = 1
        else:
            ideal = 1 / len(seen)
            share = 1 + (ideal - seen[color] / total) / ideal
        sign = -1 if self.streak_color == color else 1
        streak = SEVEN_STREAK_REDUCTION * self.streak_count * sign
        return min(2, max(0, share + streak))

    def example_pair(self, total):
        """Some pair with this total that observe() would accept now, for
        search code that branches on totals."""
        i = total - 2
        if self.mode == "balanced" and self.cards_left >= MIN_CARDS_BEFORE_RESHUFFLE:
            if self.deck[i]:
                return self.deck[i][0]
        return _FULL_DECK[i][0]

    # ----- rolling -----
    def roll(self, rng, color):
        """Draw a roll for `color` the way colonist does. `rng` needs only
        .random() (and .randint() once the dice are plain random)."""
        if self.mode != "balanced":
            pair = roll_plain_dice(rng)
            self._record(color, pair)
            return pair
        self._sevens_include(color)
        self._reshuffle_if_needed()
        w = self.weights(color)
        target = rng.random() * sum(w)
        for i, weight in enumerate(w):
            if target <= weight:
                pairs = self.deck[i]
                pair = pairs[int(rng.random() * len(pairs))]
                self._draw(i, pair)
                self._record(color, pair)
                return pair
            target -= weight
        raise RuntimeError("weighted dice deck has no card to draw")

    def observe(self, color, pair):
        """Apply a roll made elsewhere (a replay, or a live game)."""
        pair = (int(pair[0]), int(pair[1]))
        i = pair[0] + pair[1] - 2
        if self.mode == "balanced":
            self._sevens_include(color)
            self._reshuffle_if_needed()
            if self.weights(color)[i] > 0 and pair in self.deck[i]:
                self._draw(i, pair)
            else:
                self.mode = "random"
                self._cache.clear()
        self._record(color, pair)

    def _sevens_include(self, color):
        if color not in self.sevens:  # every other color's 7 odds change
            self.sevens[color] = 0
            self._cache.clear()

    def _reshuffle_if_needed(self):
        if self.cards_left < MIN_CARDS_BEFORE_RESHUFFLE:
            self.deck = list(_FULL_DECK)
            self.cards_left = DECK_SIZE
            self._cache.clear()

    def _draw(self, i, pair):
        pairs = list(self.deck[i])
        pairs.remove(pair)
        self.deck[i] = tuple(pairs)
        self.cards_left -= 1
        self._cache.clear()

    def _record(self, color, pair):
        # Recency, 7 tallies and the streak are kept in either mode, so the
        # state stays complete if the dice turn out not to be balanced.
        i = pair[0] + pair[1] - 2
        self._cache.clear()
        self.recent.append(i + 2)
        self.recent_count[i] += 1
        if len(self.recent) > RECENT_MEMORY:
            self.recent_count[self.recent.popleft() - 2] -= 1
        self.sevens.setdefault(color, 0)
        if i == SEVEN:
            self.sevens[color] += 1
            if self.streak_color == color:
                self.streak_count += 1
            else:
                self.streak_color, self.streak_count = color, 1

    # ----- copying and storage -----
    def copy(self):
        c = BalancedDice.__new__(BalancedDice)
        c.num_players = self.num_players
        c.deck = list(self.deck)  # the tuples are shared, never changed
        c._cache = dict(self._cache)
        c.cards_left = self.cards_left
        c.recent = self.recent.copy()
        c.recent_count = self.recent_count.copy()
        c.sevens = self.sevens.copy()
        c.streak_color = self.streak_color
        c.streak_count = self.streak_count
        c.mode = self.mode
        return c

    def to_json(self):
        return {
            "kind": "balanced",
            "num_players": self.num_players,
            "deck": [[list(p) for p in pairs] for pairs in self.deck],
            "cards_left": self.cards_left,
            "recent": list(self.recent),
            "sevens": [[_name(c), n] for c, n in self.sevens.items()],
            "streak": [_name(self.streak_color), self.streak_count],
            "mode": self.mode,
        }

    @staticmethod
    def from_json(doc, color_of=lambda name: name):
        d = BalancedDice(doc["num_players"])
        d.deck = [tuple(tuple(p) for p in pairs) for pairs in doc["deck"]]
        d.cards_left = doc["cards_left"]
        for t in doc["recent"]:
            d.recent.append(t)
            d.recent_count[t - 2] += 1
        d.sevens = {color_of(c): n for c, n in doc["sevens"]}
        streak_color, d.streak_count = doc["streak"]
        d.streak_color = color_of(streak_color) if streak_color else None
        d.mode = doc["mode"]
        return d


def _name(color):
    return getattr(color, "value", color)
