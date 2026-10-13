"""Why the bot loses to people: repeat opponents and the race for bonus points.

Replays every finished live game in data/human/index.jsonl and prints win
rates (with Wilson 95% intervals) for opponents met once, 2-3 and 4+ times,
by meeting number, and by who first took longest road. Prints no names:
opponents appear only as sha256(name)[:8], and only in aggregate.

    nice -n 19 python -m catanatron.colonist.loss_report [--last 500]
"""

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


class _Bonus:
    """Who first held longest road and largest army, and on which turn."""

    def __init__(self):
        self.first = {}
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        state = game.state
        for key in ("HAS_ROAD", "HAS_ARMY"):
            if key in self.first:
                continue
            for color, i in state.color_to_index.items():
                if state.player_state[f"P{i}_{key}"]:
                    self.first[key] = (color.value, state.num_turns)

    def after(self, game):
        self.final = game


def _final(game, color):
    state = game.state
    i = {c.value: j for c, j in state.color_to_index.items()}[color]
    p = state.player_state
    return {
        "road": bool(p[f"P{i}_HAS_ROAD"]),
        "army": bool(p[f"P{i}_HAS_ARMY"]),
        "vp_cards": p[f"P{i}_VICTORY_POINT_IN_HAND"],
    }


def game_facts(path):
    """Replay one capture; return its facts, or None if it doesn't replay."""
    record = convert(load_capture(path)).record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    you = you[0]
    them = next(c for c in record["seats"] if c != you)
    name = record["players"][them].get("name") or ""
    observer = _Bonus()
    if not replay(record, observer=observer).ok:
        return None
    return {
        "opp": hashlib.sha256(name.encode()).hexdigest()[:8],
        "you": you,
        "them": them,
        "first": observer.first,
        "bot_final": _final(observer.final, you),
        "human_final": _final(observer.final, them),
    }


def summarize(games, last=0):
    """Report lines for games in time order (each an index row plus its
    game_facts). Repeat counts use every game; win rates use the last ones."""
    meets = Counter(g["opp"] for g in games)
    seen = defaultdict(int)
    for g in games:
        seen[g["opp"]] += 1
        g["nth"] = seen[g["opp"]]
    window = games[-last:] if last else games
    rate = lambda sel: wilson(sum(g["bot_won"] for g in sel), len(sel))  # noqa: E731
    lines = [
        f"games {len(games)}, window {len(window)}, distinct opponents {len(meets)}"
    ]
    for label, keep in (
        ("met once", lambda m: m == 1),
        ("met 2-3 times", lambda m: 2 <= m <= 3),
        ("met 4+ times", lambda m: m >= 4),
    ):
        lines.append(
            f"repeat: {label}: bot wins {rate([g for g in window if keep(meets[g['opp']])])}"
        )
    repeat = [g for g in window if meets[g["opp"]] >= 2]
    for label, keep in (
        ("1st meeting", lambda n: n == 1),
        ("meetings 2-3", lambda n: 2 <= n <= 3),
        ("meetings 4+", lambda n: n >= 4),
    ):
        lines.append(
            f"repeat opponents, {label}: bot wins {rate([g for g in repeat if keep(g['nth'])])}"
        )
    top = [m for _, m in meets.most_common(5)]
    lines.append(f"most games against one opponent: {top}")
    to15 = [g for g in window if g["ended"] == "win"]
    for label, won, side, final in (
        ("people who won (our losses)", False, "them", "human_final"),
        ("bot wins", True, "you", "bot_final"),
    ):
        sel = [g for g in to15 if g["bot_won"] == won]
        n = len(sel)
        lines.append(f"to 15, {label}: n={n}")
        for key in ("road", "army"):
            held = sum(g[final][key] for g in sel)
            lines.append(f"  winner holds {key}: {wilson(held, n)}")
        cards = sum(g[final]["vp_cards"] for g in sel)
        any_card = sum(g[final]["vp_cards"] > 0 for g in sel)
        lines.append(
            f"  winner has a VP card: {wilson(any_card, n)}, mean {cards / max(n, 1):.2f}"
        )
        lr = [g for g in sel if "HAS_ROAD" in g["first"]]
        firsts = sum(g["first"]["HAS_ROAD"][0] == g[side] for g in lr)
        lines.append(f"  winner took longest road first: {wilson(firsts, len(lr))}")
        turns = sorted(g["first"]["HAS_ROAD"][1] for g in lr)
        if turns:
            lines.append(
                f"  median turn longest road first taken: {turns[len(turns) // 2]}"
            )
    lr = [g for g in window if "HAS_ROAD" in g["first"]]
    for label, side in (("bot", "you"), ("person", "them")):
        sel = [g for g in lr if g["first"]["HAS_ROAD"][0] == g[side]]
        lines.append(f"{label} took longest road first: bot wins {rate(sel)}")
    for label, keep in (
        ("first meetings", lambda n: n == 1),
        ("repeat meetings", lambda n: n >= 2),
    ):
        sel = [g for g in lr if keep(g["nth"])]
        person = sum(g["first"]["HAS_ROAD"][0] == g["them"] for g in sel)
        lines.append(
            f"person took longest road first, {label}: {wilson(person, len(sel))}"
        )
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--index", default="data/human/index.jsonl")
    parser.add_argument("--captures", default="captures")
    parser.add_argument("--last", type=int, default=0, help="window of latest games")
    args = parser.parse_args(argv)
    with open(args.index) as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    # Rebuilds append rows, so keep one per capture; names are UTC timestamps.
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    games, skipped = [], 0
    for capture in sorted(rows):
        row = rows[capture]
        path = os.path.join(args.captures, row["account"], capture)
        try:
            facts = game_facts(path)
        except (OSError, ValueError, KeyError, IndexError):
            facts = None
        if facts is None:
            skipped += 1
            continue
        games.append({**row, **facts})
    print(f"skipped {skipped} captures that didn't replay")
    for line in summarize(games, args.last):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
