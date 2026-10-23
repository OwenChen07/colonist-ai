"""Compare two models on automatically selected, verified human-game decisions.

The input index chooses one split. Captures stay private; output has aggregate
counts only. The checks are deliberately narrow: taking a road while a
settlement is legal is descriptive, whereas skipping an immediate legal win
or choosing the flagged final road is a stronger tactical warning.
"""

import argparse
import json
import random
from collections import Counter

from catanatron.colonist import encode as E
from catanatron.colonist.bonus_audit import (
    immediate_winning_actions,
    indexed_paths,
)
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.judgment_policy import eligible_road_flags
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType
from catanatron.models.player import Color


def position_flags(game, color):
    actions = game.playable_actions
    if len(actions) < 2:
        return {}
    flags = {}
    road_flags = eligible_road_flags(game.state, actions)
    if road_flags:
        flags["last_road_no_immediate_gain"] = road_flags
    wins = immediate_winning_actions(game, color)
    if wins:
        flags["immediate_win"] = {E.action_key(a) for a in wins}
    if any(a.action_type == ActionType.BUILD_SETTLEMENT for a in actions) and any(
        a.action_type == ActionType.BUILD_ROAD for a in actions
    ):
        flags["road_over_available_settlement"] = {
            E.action_key(a) for a in actions if a.action_type == ActionType.BUILD_ROAD
        }
    return flags


class _Collector:
    def __init__(self, color, per_game):
        self.color = color
        self.per_game = per_game
        self.by_turn = {}

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color:
            return
        turn = game.state.num_turns
        flags = position_flags(game, self.color)
        if not flags:
            return
        priority = (
            3
            if "immediate_win" in flags
            else 2 if "last_road_no_immediate_gain" in flags else 1
        )
        previous = self.by_turn.get(turn)
        if previous is not None:
            if priority > previous[0]:
                self.by_turn[turn] = (priority, game.copy(), decision, flags)
            return
        if len(self.by_turn) < self.per_game:
            self.by_turn[turn] = (priority, game.copy(), decision, flags)
            return
        weakest_turn, weakest = min(
            self.by_turn.items(), key=lambda item: (item[1][0], item[0])
        )
        if priority > weakest[0]:
            del self.by_turn[weakest_turn]
            self.by_turn[turn] = (priority, game.copy(), decision, flags)

    @property
    def positions(self):
        return [
            (game, decision, flags)
            for _, game, decision, flags in sorted(
                self.by_turn.values(), key=lambda row: -row[0]
            )
        ]

    def after(self, game):
        pass


def positions_in_capture(path, per_game):
    conversion = convert(load_capture(path))
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return []
    record = conversion.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return []
    observer = _Collector(Color[you[0]], per_game)
    report = replay(record, observer=observer)
    return observer.positions if report.ok else []


def choose(game, net, simulations, seed):
    search = Search(net, simulations=simulations, rng=random.Random(seed), noise=False)
    action, _ = search.run(game)
    return E.action_key(action)


def compare(baseline, candidate, paths, simulations=100, max_positions=100, per_game=2):
    nets = (Net.load(baseline), Net.load(candidate))
    counts = Counter()
    for path in paths:
        if counts["positions"] >= max_positions:
            break
        counts["captures_scanned"] += 1
        try:
            positions = positions_in_capture(path, per_game)
        except (OSError, ValueError, KeyError, IndexError):
            continue
        counts["verified_captures_with_positions"] += bool(positions)
        for game, recorded, flags in positions:
            if counts["positions"] >= max_positions:
                break
            index = counts["positions"]
            counts["positions"] += 1
            chosen = [
                choose(game, net, simulations, 7717 + 1009 * index) for net in nets
            ]
            counts["decisions_changed"] += chosen[0] != chosen[1]
            for label, selected in zip(("baseline", "candidate"), chosen):
                for category, marked in flags.items():
                    counts[f"{category}_positions"] += int(label == "baseline")
                    if category == "immediate_win":
                        counts[f"{label}_{category}_missed"] += selected not in marked
                    else:
                        counts[f"{label}_{category}_chosen"] += selected in marked
                counts[f"{label}_recorded_agreement"] += selected == E.action_key(
                    recorded
                )
    return dict(counts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--index", default="data/human/index.jsonl")
    parser.add_argument("--captures", default="captures")
    parser.add_argument("--split", choices=("train", "holdout"), default="holdout")
    parser.add_argument("--last", type=int, default=300)
    parser.add_argument("--positions", type=int, default=100)
    parser.add_argument("--per-game", type=int, default=2)
    parser.add_argument("--simulations", type=int, default=100)
    args = parser.parse_args(argv)
    paths = indexed_paths(args.index, args.captures, args.split, args.last)
    result = compare(
        args.baseline,
        args.candidate,
        paths,
        args.simulations,
        args.positions,
        args.per_game,
    )
    print(
        json.dumps(
            {"split": args.split, "simulations": args.simulations, **result},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
