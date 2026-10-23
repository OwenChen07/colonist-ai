"""Stated minus won at bot turns 10/15/20 in 2300+ games, sliced by what the person holds.

Reads peak_story.json (replayed 2300+ games). Slices: person's hidden dev cards,
Longest Road race (lengths within 2, neither or either holding), person's cities,
robber steals on the bot so far.
"""

import json
import math
from pathlib import Path

G = json.loads(Path("data/orchestrator/scratch/late-game/peak_story.json").read_text())


def ci(xs):
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, 0.0
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


def row(label, rows):
    if len(rows) < 8:
        print(f"    {label:28s} n {len(rows):3d}  (too few)")
        return
    gap = [s - 100 * w for s, w in rows]
    m, h = ci(gap)
    st = sum(s for s, _ in rows) / len(rows)
    wn = 100 * sum(w for _, w in rows) / len(rows)
    print(
        f"    {label:28s} n {len(rows):3d}  said {st:5.1f}  won {wn:5.1f}  gap {m:+5.1f} [{m-h:+5.1f},{m+h:+5.1f}]"
    )


def main():
    hi = [g for g in G if g.get("replay") and g["mmr"] and g["mmr"] >= 2300]
    print(f"2300+ replayed games: {len(hi)}")
    for K in (10, 15, 20):
        print(f"\nbot turn {K}")
        data = []
        for g in hi:
            s = g["at_turn"].get(str(K))
            snap = g["replay"]["snap"].get(str(K))
            if s is None or snap is None:
                continue
            steals = sum(
                1
                for k, w, kind in g["replay"]["ev"]
                if k <= K and w == "opp" and kind == "robber_on_other"
            )
            data.append((s, g["won"], snap["bot"], snap["opp"], steals))
        row("all", [(s, w) for s, w, *_ in data])
        print("  person's hidden dev cards")
        for lo, hi_, lab in ((0, 0, "0"), (1, 1, "1"), (2, 99, "2+")):
            row(lab, [(s, w) for s, w, b, o, _ in data if lo <= o["devhand"] <= hi_])
        print("  Longest Road")
        row("person holds", [(s, w) for s, w, b, o, _ in data if o["road"]])
        row("bot holds", [(s, w) for s, w, b, o, _ in data if b["road"]])
        row(
            "nobody, lengths within 2",
            [
                (s, w)
                for s, w, b, o, _ in data
                if not b["road"]
                and not o["road"]
                and abs(b["roadlen"] - o["roadlen"]) <= 2
            ],
        )
        row(
            "bot holds, person within 2",
            [
                (s, w)
                for s, w, b, o, _ in data
                if b["road"] and o["roadlen"] >= b["roadlen"] - 2
            ],
        )
        print("  person's cities")
        for c in (0, 1, 2):
            row(
                f"{c}{'+' if c == 2 else ''}",
                [
                    (s, w)
                    for s, w, b, o, _ in data
                    if (o["cities"] >= 2 if c == 2 else o["cities"] == c)
                ],
            )
        print("  person's cities minus bot's")
        row(
            "person ahead",
            [(s, w) for s, w, b, o, _ in data if o["cities"] > b["cities"]],
        )
        row("even", [(s, w) for s, w, b, o, _ in data if o["cities"] == b["cities"]])
        row(
            "bot ahead", [(s, w) for s, w, b, o, _ in data if o["cities"] < b["cities"]]
        )
        print("  robber steals on the bot so far")
        for lo, hi_, lab in ((0, 0, "0"), (1, 1, "1"), (2, 99, "2+")):
            row(lab, [(s, w) for s, w, b, o, st in data if lo <= st <= hi_])
        print("  Largest Army")
        row("person holds", [(s, w) for s, w, b, o, _ in data if o["army"]])
        row(
            "person knights >= bot's",
            [
                (s, w)
                for s, w, b, o, _ in data
                if not o["army"]
                and not b["army"]
                and o["knights"] >= b["knights"]
                and o["knights"] > 0
            ],
        )


if __name__ == "__main__":
    main()
