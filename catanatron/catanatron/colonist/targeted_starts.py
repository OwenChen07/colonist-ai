"""Make a paired self-play start file enriched for public tactical races.

The input must be the frozen, deidentified training-split starts file. Entries
stay in the same order, so a self-play seed chooses the same human game in both
arms. Only selected entries' turn-start cuts change. No holdout data is read.
"""

import argparse
import gzip
import hashlib
import json
import os

from catanatron.colonist.replay import replay
from catanatron.colonist.starts import load
from catanatron.models.enums import ActionType
from catanatron.state_functions import (
    get_largest_army,
    get_longest_road_color,
    get_played_dev_cards,
    get_visible_victory_points,
    player_key,
)


def features(state):
    """Public tactical situations at the beginning of a turn."""
    a, b = state.colors
    board = state.board
    lengths = [board.road_lengths[c] for c in (a, b)]
    knights = [get_played_dev_cards(state, c, "KNIGHT") for c in (a, b)]
    roads_left = [
        state.player_state[f"{player_key(state, c)}_ROADS_AVAILABLE"] for c in (a, b)
    ]
    settlements_left = [
        state.player_state[f"{player_key(state, c)}_SETTLEMENTS_AVAILABLE"]
        for c in (a, b)
    ]
    road_race = bool(
        get_longest_road_color(state) is not None
        and max(lengths) >= 5
        and abs(lengths[0] - lengths[1]) <= 2
    )
    road_scarcity = bool(max(lengths) >= 5 and min(roads_left) <= 2)
    army_race = bool(
        get_largest_army(state)[0] is not None
        and max(knights) >= 3
        and abs(knights[0] - knights[1]) <= 1
    )
    settlement_race = bool(
        max(get_visible_victory_points(state, c) for c in (a, b)) >= 8
        and any(
            settlements_left[i] > 0 and board.buildable_node_ids(c)
            for i, c in enumerate((a, b))
        )
    )
    return {
        "road_race": road_race,
        "road_scarcity": road_scarcity,
        "army_race": army_race,
        "settlement_race": settlement_race,
    }


class _TacticalCuts:
    def __init__(self, allowed):
        self.allowed = set(allowed)
        self.index = 0
        self.cuts = []
        self.counts = {
            k: 0 for k in ("road_race", "road_scarcity", "army_race", "settlement_race")
        }

    def before(self, game):
        pass

    def step(self, game, decision):
        if self.index in self.allowed and decision.action_type == ActionType.ROLL:
            tags = features(game.state)
            if any(tags.values()):
                self.cuts.append(self.index)
                for name, found in tags.items():
                    self.counts[name] += int(found)
        self.index += 1

    def after(self, game):
        pass


def selected(index, fraction, salt):
    digest = hashlib.sha256(f"{salt}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < fraction


def build(source, output, fraction=0.4, salt="tactical-starts-v1"):
    if not 0 <= fraction <= 1:
        raise ValueError("fraction must be in [0, 1]")
    entries = load(source)
    counts = {"entries": len(entries), "eligible": 0, "changed": 0, "target_cuts": 0}
    for name in ("road_race", "road_scarcity", "army_race", "settlement_race"):
        counts[name] = 0
    tmp = output + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    try:
        with gzip.open(tmp, "wt") as stream:
            for i, entry in enumerate(entries):
                if selected(i, fraction, salt):
                    observer = _TacticalCuts(entry["cuts"])
                    report = replay(entry, observer=observer)
                    if not report.ok:
                        raise ValueError(
                            f"training start {i} did not replay: {report.errors[:1]}"
                        )
                    if observer.cuts:
                        counts["eligible"] += 1
                        counts["target_cuts"] += len(observer.cuts)
                        for name, value in observer.counts.items():
                            counts[name] += value
                        if observer.cuts != entry["cuts"]:
                            entry = {
                                **entry,
                                "cuts": observer.cuts,
                                "source_cut_count": len(entry["cuts"]),
                            }
                            counts["changed"] += 1
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        os.replace(tmp, output)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--fraction", type=float, default=0.4)
    parser.add_argument("--salt", default="tactical-starts-v1")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build(args.source, args.output, args.fraction, args.salt), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
