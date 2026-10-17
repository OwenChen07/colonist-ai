"""Replay game records through the simulator: the Phase 0 gate.

    python -m catanatron.colonist.replay records/*.json

A record passes if every logged move is legal when it is made, every check
(card counts, points) matches, and the winner matches. The dice model is
followed along the way; if a roll is impossible under balanced dice, the
report says so, since that means the game did not use them.
"""

import argparse
import glob
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from catanatron.game import Game
from catanatron.models.enums import RESOURCES, Action, ActionRecord, ActionType
from catanatron.models.actions import generate_playable_actions
from catanatron.models.player import Color, Player
from catanatron.serialization import _tupled, action_from_json, map_from_json
from catanatron.colonist.record import load, resolve_hidden_draws, snapshot

#: Logged with their outcome filled in; decided with no value.
DECIDED_WITHOUT_VALUE = {ActionType.ROLL, ActionType.BUY_DEVELOPMENT_CARD}


class RecordedPlayer(Player):
    """A seat whose moves come from the record, never from decide()."""

    def decide(self, game, playable_actions):
        raise RuntimeError("a recorded player does not decide")


@dataclass
class ReplayReport:
    name: str
    entries: int = 0
    applied: int = 0
    errors: List[str] = field(default_factory=list)
    dice_mode: Optional[str] = None
    dice_broke_at: Optional[int] = None
    winner: Optional[str] = None

    @property
    def ok(self):
        return not self.errors and self.applied == self.entries

    def summary(self):
        status = "OK  " if self.ok else "FAIL"
        dice = self.dice_mode or "plain"
        if self.dice_broke_at is not None:
            dice += (
                f" (not balanced: roll at entry {self.dice_broke_at} was impossible)"
            )
        line = (
            f"{status} {self.name}: {self.applied}/{self.entries} entries, dice {dice}"
        )
        if self.winner:
            line += f", winner {self.winner}"
        return "\n".join([line] + [f"     - {e}" for e in self.errors])


def game_winner(record, report) -> Optional[str]:
    """The winner on points, or else the player who didn't resign (or leave).

    A resigned game never reaches a winning position, so report.winner is None.
    """
    if report.winner:
        return report.winner
    others = [c for c in record.get("seats", []) if c != record.get("resigned")]
    return others[0] if record.get("resigned") and len(others) == 1 else None


def game_for_record(record) -> Game:
    """A fresh game on the record's board, seats and rules."""
    rules = record["rules"]
    seats = [Color[c] for c in record["seats"]]
    game = Game(
        [RecordedPlayer(c) for c in seats],
        seed=0,
        catan_map=map_from_json(record["map"]),
        vps_to_win=rules["vps_to_win"],
        discard_limit=rules["discard_limit"],
        friendly_robber=rules["friendly_robber"],
        dice=rules.get("dice", "random"),
    )
    s = game.state
    # Game seats players in a random order; a record's order is fixed.
    by_color = {p.color: p for p in s.players}
    s.players = [by_color[c] for c in seats]
    s.colors = tuple(seats)
    s.color_to_index = {c: i for i, c in enumerate(seats)}
    s.random = None  # a replay takes every outcome from the log
    if record.get("robber"):
        s.board.robber_coordinate = tuple(record["robber"])
    game.playable_actions = generate_playable_actions(s)
    return game


def decision_of(action: Action, playable_actions) -> Action:
    if action.action_type in DECIDED_WITHOUT_VALUE:
        return Action(action.color, action.action_type, None)
    if action.action_type == ActionType.BUILD_ROAD and action not in playable_actions:
        # An edge is the same edge in either node order.
        flipped = Action(
            action.color, action.action_type, tuple(reversed(action.value))
        )
        if flipped in playable_actions:
            return flipped
    if (
        action.action_type == ActionType.PLAY_YEAR_OF_PLENTY
        and action not in playable_actions
        and isinstance(action.value, tuple)
    ):
        # colonist lists the two cards in the order they were picked;
        # Catanatron's moves list them in resource order.
        ordered = Action(
            action.color,
            action.action_type,
            tuple(sorted(action.value, key=RESOURCES.index)),
        )
        if ordered in playable_actions:
            return ordered
    return action


def replay(record, name="record", observer=None) -> ReplayReport:
    log = resolve_hidden_draws(record)
    report = ReplayReport(name=name, entries=len(log))
    if record.get("incomplete"):
        report.errors.append(f"conversion stopped early: {record['incomplete']}")
    checks = {}
    for check in record.get("checks", []):
        checks.setdefault(check["at"], []).append(check)

    game = game_for_record(record)
    if observer is not None:
        observer.before(game)
    dice = game.state.dice
    _run_checks(game, checks.get(0, []), report)
    for i, (payload, result) in enumerate(log):
        action = action_from_json(payload)
        decision = decision_of(action, game.playable_actions)
        if decision not in game.playable_actions:
            legal = sorted({a.action_type.value for a in game.playable_actions})
            report.errors.append(
                f"entry {i}: {payload} is not legal here (legal move types: {legal})"
            )
            break
        mode_before = dice.mode if dice is not None else None
        try:
            if observer is not None:
                observer.step(game, decision)
            game.execute(
                decision,
                validate_action=False,
                action_record=ActionRecord(action, _tupled(result)),
            )
        except Exception as e:  # the engine refused the outcome itself
            report.errors.append(f"entry {i}: {payload} failed: {e!r}")
            break
        report.applied = i + 1
        if dice is not None and mode_before == "balanced" and dice.mode != "balanced":
            report.dice_broke_at = i
        _run_checks(game, checks.get(i + 1, []), report)

    if dice is not None:
        report.dice_mode = dice.mode
    winner = game.winning_color()
    if observer is not None:
        observer.after(game)
    report.winner = winner.value if winner else None
    if report.ok and record.get("winner") and record["winner"] != report.winner:
        report.errors.append(
            f"winner: record says {record['winner']}, replay gives {report.winner}"
        )
    return report


def _run_checks(game, checks, report):
    for check in checks:
        actual = snapshot(game.state, Color[check["color"]], check["at"])
        for key, expected in check.items():
            if key in ("at", "color"):
                continue
            if actual.get(key) != expected:
                report.errors.append(
                    f"check after {check['at']} entries, {check['color']} {key}: "
                    f"record {expected}, replay {actual.get(key)}"
                )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="record files or globs")
    args = parser.parse_args(argv)
    paths = sorted({p for pattern in args.paths for p in glob.glob(pattern)})
    if not paths:
        parser.error("no record files matched")
    reports = [replay(load(p), name=p) for p in paths]
    for r in reports:
        print(r.summary())
    passed = sum(r.ok for r in reports)
    print(f"\n{passed}/{len(reports)} records replayed cleanly")
    return 0 if passed == len(reports) else 1


if __name__ == "__main__":
    sys.exit(main())
