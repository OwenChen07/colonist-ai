"""Audit last-road spending and bonus races in private, verified 1v1 captures.

Only aggregate counts leave this command. A flagged road has no *immediate*
tactical gain under a deliberately narrow, public-board check; it is a review
candidate, not a label saying that the move lost the game.

    python -m catanatron.colonist.bonus_audit --last 300
    python -m catanatron.colonist.bonus_audit --capture captures/bot3/game.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from catanatron.colonist.bonus_tactics import army_race, paid_road_facts
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType
from catanatron.models.player import Color
from catanatron.state_functions import get_visible_victory_points


WINNING_BUILD_TYPES = {
    ActionType.BUILD_ROAD,
    ActionType.BUILD_SETTLEMENT,
    ActionType.BUILD_CITY,
    ActionType.PLAY_KNIGHT_CARD,
}


def immediate_winning_actions(game, color):
    """Legal deterministic actions that end the game immediately."""
    if get_visible_victory_points(game.state, color) < game.vps_to_win - 2:
        return ()
    wins = []
    for action in game.playable_actions:
        if action.color != color or action.action_type not in WINNING_BUILD_TYPES:
            continue
        branch = game.copy()
        branch.execute(action, validate_action=False)
        if branch.winning_color() == color:
            wins.append(action)
    return tuple(wins)


class _Audit:
    def __init__(self, color):
        self.color = color
        self.roads = []
        self.army_ties_held = 0
        self.army_ties_to_steal = 0
        self.road_with_settlement_available = 0
        self.immediate_win_opportunities = 0
        self.missed_immediate_win_turns = 0
        self._deferred_win = False

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color:
            if self._deferred_win:
                self.missed_immediate_win_turns += 1
                self._deferred_win = False
            return
        winning = immediate_winning_actions(game, self.color)
        if winning:
            self.immediate_win_opportunities += 1
            self._deferred_win = decision not in winning
        elif decision.action_type == ActionType.END_TURN and self._deferred_win:
            self.missed_immediate_win_turns += 1
            self._deferred_win = False
        race = army_race(game.state, self.color)
        if race.holder == self.color and race.mine == race.theirs:
            self.army_ties_held += 1
        elif race.holder is not None and race.mine == race.theirs:
            self.army_ties_to_steal += 1
        if decision.action_type == ActionType.BUILD_ROAD:
            if any(
                a.action_type == ActionType.BUILD_SETTLEMENT
                for a in game.playable_actions
            ):
                self.road_with_settlement_available += 1
            facts = paid_road_facts(game.state, decision)
            if facts is not None:
                self.roads.append(facts)

    def after(self, game):
        pass


def capture_counts(path):
    """Return aggregate facts only when conversion and full replay both pass."""
    conversion = convert(load_capture(path))
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return None
    record = conversion.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    observer = _Audit(Color[you[0]])
    if not replay(record, observer=observer).ok:
        return None
    return {
        "paid_roads": len(observer.roads),
        "last_piece_roads": sum(r["last_piece"] for r in observer.roads),
        "no_immediate_tactical_gain": sum(
            r["no_immediate_tactical_gain"] for r in observer.roads
        ),
        "army_tie_decisions_held": observer.army_ties_held,
        "army_tie_decisions_to_steal": observer.army_ties_to_steal,
        "road_with_settlement_available": observer.road_with_settlement_available,
        "immediate_win_opportunities": observer.immediate_win_opportunities,
        "missed_immediate_win_turns": observer.missed_immediate_win_turns,
    }


def indexed_paths(index, captures, split, last):
    rows = {}
    with open(index) as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if row.get("status") == "ok" and row.get("split") == split:
                    rows[(row["account"], row["capture"])] = None
    keys = sorted(rows, key=lambda key: key[1], reverse=True)
    for account, capture in keys[:last] if last else keys:
        yield Path(captures) / account / capture


def audit(paths):
    totals = Counter()
    for path in paths:
        totals["captures_scanned"] += 1
        try:
            counts = capture_counts(path)
        except (OSError, ValueError, KeyError, IndexError):
            counts = None
        if counts is None:
            totals["captures_not_verified"] += 1
        else:
            totals["verified_games"] += 1
            totals.update(counts)
    return dict(totals)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--index", default="data/human/index.jsonl")
    parser.add_argument("--captures", default="captures")
    parser.add_argument("--split", choices=("train", "holdout"), default="train")
    parser.add_argument("--last", type=int, default=300)
    parser.add_argument(
        "--capture", help="audit one private capture, without printing it"
    )
    args = parser.parse_args(argv)
    paths = (
        [Path(args.capture)]
        if args.capture
        else indexed_paths(args.index, args.captures, args.split, args.last)
    )
    print(json.dumps(audit(paths), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
