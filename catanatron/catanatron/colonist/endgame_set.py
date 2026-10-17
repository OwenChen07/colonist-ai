"""Count endgame positions where a +1 VP build was one YoP/trade away.

    python -m catanatron.colonist.endgame_set captures/bot3/bridge-*.json

For each capture the bot played, replays the game and, at each of the bot's
decisions where `endgame_policy` applies (near the target, no direct build,
a funding move unlocks one), records whether the move played was on the path.
Output is aggregate counts only; captures stay private.
"""

import argparse
import json
from collections import Counter

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.endgame_policy import build_path_actions
from catanatron.colonist.replay import replay
from catanatron.models.player import Color


class _Collector:
    def __init__(self, color):
        self.color = color
        self.rows = []

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color or len(game.playable_actions) < 2:
            return
        path = build_path_actions(game, game.playable_actions)
        if path:
            self.rows.append(
                {
                    "turn": game.state.num_turns,
                    "followed": E.action_key(decision)
                    in {E.action_key(a) for a in path},
                    "path": len(path),
                    "legal": len(game.playable_actions),
                }
            )

    def after(self, game):
        pass


def scan(path):
    """(rows, won) for one capture, or None when it can't be replayed."""
    conversion = convert(load_capture(path))
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return None
    record = conversion.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    observer = _Collector(Color[you[0]])
    report = replay(record, observer=observer)
    if not report.ok:
        return None
    return observer.rows, report.winner == you[0]


def _wilson(wins, n, z=1.96):
    """(rate, low, high): Wilson 95% interval for wins out of n."""
    p = wins / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return p, mid - half, mid + half


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("captures", nargs="+")
    args = parser.parse_args(argv)
    counts = Counter()
    for path in args.captures:
        try:
            result = scan(path)
        except (OSError, ValueError, KeyError, IndexError):
            result = None
        if result is None:
            counts["skipped"] += 1
            continue
        rows, won = result
        counts["games"] += 1
        counts["games_with_position"] += bool(rows)
        missed = [r for r in rows if not r["followed"]]
        counts["decisions"] += len(rows)
        counts["decisions_missed"] += len(missed)
        counts["turns_with_miss"] += len({r["turn"] for r in missed})
        counts["games_with_miss"] += bool(missed)
        counts["lost_games"] += not won
        counts["lost_games_with_miss"] += bool(missed) and not won
        if rows:
            arm = "miss" if missed else "followed"
            counts[f"position_games_{arm}"] += 1
            counts[f"position_wins_{arm}"] += won
    out = dict(counts)
    for arm in ("miss", "followed"):
        n, w = counts[f"position_games_{arm}"], counts[f"position_wins_{arm}"]
        if n:
            out[f"win_rate_{arm}"] = [round(x, 3) for x in _wilson(w, n)]
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
