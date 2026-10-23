"""Research's E55 questions on the opponent_exact export (train split):
(1) the share of the person's decisions and turns kept;
(2) cards held at turn 60+ in kept decisions against all decisions.

    .venv/bin/python data/orchestrator/scratch/live-loss/exact_bias.py [LIMIT]
"""

import json
import math
import os
import sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, "catanatron")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.human_data import (  # noqa: E402
    UNKNOWN,
    DevHands,
    held_cards,
)
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import VICTORY_POINT  # noqa: E402
from catanatron.state_functions import player_num_dev_cards  # noqa: E402

ROOT = "/home/owen/colonist-ai"


class Obs:
    def __init__(self, color, hands):
        self.color, self.hands = color, hands
        self.index = 0
        self.rows = []  # (turn, held, kind, actions if exact else None)

    def before(self, game):
        pass

    def step(self, game, decision):
        i, self.index = self.index, self.index + 1
        s = game.state
        if s.current_color().value != self.color or s.is_initial_build_phase:
            return
        if len(game.playable_actions) <= 1:
            return
        kind, hand = self.hands.at(i)
        held = player_num_dev_cards(s, s.current_color())
        if kind == "exact" and held_cards(s, s.current_color()) != hand:
            kind = "mismatch"
        acts = None
        if kind == "exact":
            acts = sum(n for k, n in hand.items() if k not in (VICTORY_POINT, UNKNOWN))
        elif kind == "none":
            acts = 0
        self.rows.append((s.num_turns, held, kind, acts))

    def after(self, game):
        pass


def one(row):
    path = os.path.join(ROOT, "captures", row["account"], row["capture"])
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]][0]
        them = [c for c in record["seats"] if c != you][0]
        hands = DevHands(record, them)
        obs = Obs(them, hands)
        if not replay(record, observer=obs).ok:
            return {"err": "replay"}
        revealed = them in (record.get("hidden_vp_cards") or {})
        return {
            "rows": obs.rows,
            "vp": hands.vp,
            "revealed": revealed,
            "unknown": hands.unknown,
            "won": row.get("bot_won") is False,
        }
    except Exception as e:  # one bad capture must not stop the rest
        return {"err": type(e).__name__}


def ci(k, n):
    """Wilson 95% interval, in percent."""
    if not n:
        return "n=0"
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}]"


def mean_ci(xs):
    n = len(xs)
    if n < 2:
        return f"n={n}"
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return f"{m:.3f} ± {1.96 * sd / math.sqrt(n):.3f} (n={n})"


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = []
    with open(os.path.join(ROOT, "data/human/index.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") == "ok" and r.get("split") == "train":
                rows.append(r)
    rows = rows[:limit]
    with Pool(6) as pool:
        res = pool.map(one, rows, chunksize=4)
    errs = Counter(r["err"] for r in res if "err" in r)
    good = [r for r in res if "err" not in r]
    print(f"games {len(rows)}, replayed {len(good)}, errors {dict(errs)}")
    print(f"vp reveal present for the person in {sum(r['revealed'] for r in good)}")

    for tag, sel in (("all", good), ("person won", [r for r in good if r["won"]])):
        dec = [x for r in sel for x in r["rows"]]
        kinds = Counter(x[2] for x in dec)
        kept = kinds["none"] + kinds["exact"]
        print(f"\n== {tag}: {len(sel)} games, {len(dec)} decisions {dict(kinds)}")
        print(f"(1) decisions kept: {kept} of {len(dec)} = {ci(kept, len(dec))}")
        # A turn is kept when every decision in it is kept.
        tk = tn = 0
        for r in sel:
            by = {}
            for t, _, k, _ in r["rows"]:
                by.setdefault(t, []).append(k in ("none", "exact"))
            tn += len(by)
            tk += sum(all(v) for v in by.values())
        print(f"    turns fully kept: {tk} of {tn} = {ci(tk, tn)}")
        for lo, hi in ((0, 30), (30, 60), (60, 90), (90, 999)):
            d = [x for x in dec if lo <= x[0] < hi]
            k = sum(x[2] in ("none", "exact") for x in d)
            print(f"    turn {lo}-{hi}: kept {k} of {len(d)} = {ci(k, len(d))}")

        late = [x for x in dec if x[0] >= 60]
        lk = [x for x in late if x[2] in ("none", "exact")]
        print(f"(2) turn 60+: {len(late)} decisions, {len(lk)} kept")
        print(f"    cards held (all kinds), all: {mean_ci([x[1] for x in late])}")
        print(f"    cards held (all kinds), kept: {mean_ci([x[1] for x in lk])}")
        print(f"    action cards held, kept: {mean_ci([x[3] for x in lk])}")
        # All decisions: action cards are between held - possible VP and held.
        for need in (1, 2, 3):
            a = sum(x[1] >= need for x in late)
            b = sum(x[1] >= need for x in lk)
            print(
                f"    {need}+ cards held: all {ci(a, len(late))}, kept {ci(b, len(lk))}"
            )
        dropped = [x for x in late if x[2] not in ("none", "exact")]
        print(
            f"    dropped at 60+: {dict(Counter(x[2] for x in dropped))}, "
            f"cards held there {mean_ci([x[1] for x in dropped])}"
        )

        # Hoarders: a card never played and not a revealed VP card.
        hoard = [r for r in sel if r["revealed"] and r["unknown"] > 0]
        nohoard = [r for r in sel if r["revealed"] and r["unknown"] == 0]
        for name, g in (
            ("held an action card to the end", hoard),
            ("no such card", nohoard),
            ("no reveal", [r for r in sel if not r["revealed"]]),
        ):
            d = [x for r in g for x in r["rows"]]
            k = sum(x[2] in ("none", "exact") for x in d)
            d6 = [x for x in d if x[0] >= 60]
            k6 = sum(x[2] in ("none", "exact") for x in d6)
            print(
                f"    games {name}: {len(g)}; decisions kept {ci(k, len(d))}; "
                f"at 60+ {ci(k6, len(d6))}"
            )
        kept_cards = Counter(x[3] for x in lk)
        print(f"    action cards in kept 60+ hands: {dict(sorted(kept_cards.items()))}")


if __name__ == "__main__":
    main()
