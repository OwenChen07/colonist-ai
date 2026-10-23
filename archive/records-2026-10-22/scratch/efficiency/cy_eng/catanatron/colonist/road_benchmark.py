"""Offline benchmark for spending more search on contested longest road.

The decision audit uses only verified human-game captures from the training
split. It prints aggregate results, never names, chat, hands, or capture paths.
It measures changed decisions and search cost; agreement with the recorded
move is descriptive, not a measure of playing strength.

    python -m catanatron.colonist.road_benchmark runs/g1/best.npz \
        --index data/human/index.jsonl --captures captures --last 600

For a win-rate test, use paired boards and swap seats. Set --control-sims to
match the adaptive player's measured thinking time in a pilot; the output
reports the actual ratio so an unequal-compute comparison is visible.

    python -m catanatron.colonist.road_benchmark runs/g1/best.npz \
        --max-positions 0 --games 1000 --control-sims 900 --workers 8
"""

import argparse
import json
import math
import random
import statistics
import sys
import time
from functools import partial
from pathlib import Path

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.evaluate import match
from catanatron.colonist.mcts import NetPlayer, Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionPrompt
from catanatron.state_functions import (
    get_longest_road_length,
    get_visible_victory_points,
)


def contested(state, min_road=5, max_gap=2, min_vp=10):
    """A public, late road race. Use visible points, never hidden VP cards."""
    if len(state.colors) != 2 or state.current_prompt != ActionPrompt.PLAY_TURN:
        return False
    a, b = state.colors
    roads = (get_longest_road_length(state, a), get_longest_road_length(state, b))
    return (
        min(roads) >= min_road
        and abs(roads[0] - roads[1]) <= max_gap
        and max(
            get_visible_victory_points(state, a),
            get_visible_victory_points(state, b),
        )
        >= min_vp
    )


class _Positions:
    def __init__(self, color, per_game):
        self.color = color
        self.per_game = per_game
        self.positions = []
        self.turns = set()

    def before(self, game):
        pass

    def step(self, game, decision):
        state = game.state
        turn = state.num_turns
        if (
            len(self.positions) < self.per_game
            and decision.color == self.color
            and turn not in self.turns
            and len(game.playable_actions) > 1
            and contested(state)
        ):
            self.positions.append((game.copy(), decision))
            self.turns.add(turn)

    def after(self, game):
        pass


def positions_in_capture(path, per_game):
    """Return positions only after the whole capture has replayed cleanly."""
    conversion = convert(load_capture(path))
    record = conversion.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return []
    from catanatron.models.player import Color

    observer = _Positions(Color[you[0]], per_game)
    report = replay(record, observer=observer)
    return observer.positions if report.ok else []


def indexed_captures(index, captures, last):
    """Yield unique, verified training captures newest first."""
    rows = {}
    with open(index) as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if row.get("status") == "ok" and row.get("split") == "train":
                    rows[(row["account"], row["capture"])] = row
    keys = sorted(rows, key=lambda key: key[1], reverse=True)
    for account, capture in keys[:last] if last else keys:
        yield Path(captures) / account / capture


def search_once(game, net, simulations, seed, time_limit=None):
    search = Search(net, simulations=simulations, rng=random.Random(seed), noise=False)
    start = time.perf_counter()
    action, root = search.run(game, time_limit=time_limit)
    elapsed = time.perf_counter() - start
    _, value = search.root_targets(root)
    return {
        "key": E.action_key(action),
        "type": action.action_type.value,
        "seconds": elapsed,
        "simulations": search.used,
        "value": value,
    }


def audit(
    net_path, index, captures, last, max_positions, per_game, base, deep, time_limit
):
    net = Net.load(net_path)
    rows = []
    scanned = valid = 0
    for path in indexed_captures(index, captures, last):
        scanned += 1
        try:
            positions = positions_in_capture(path, per_game)
        except (OSError, ValueError, KeyError, IndexError):
            continue
        if positions:
            valid += 1
        for game, recorded in positions:
            # The same hidden-card redeal is used at both budgets. The seed
            # contains no player identity and is stable for this audit order.
            seed = len(rows) + 1701
            low = search_once(game, net, base, seed, time_limit)
            high = search_once(game, net, deep, seed, time_limit)
            rows.append((low, high, E.action_key(recorded)))
            if len(rows) >= max_positions:
                break
        if len(rows) >= max_positions:
            break
    n = len(rows)
    result = {
        "captures_scanned": scanned,
        "captures_with_positions": valid,
        "positions": n,
        "base_simulations": base,
        "deep_simulations": deep,
        "time_limit_seconds": time_limit,
        "decisions_changed": sum(a["key"] != b["key"] for a, b, _ in rows),
        "base_agrees_with_recorded": sum(a["key"] == r for a, _, r in rows),
        "deep_agrees_with_recorded": sum(b["key"] == r for _, b, r in rows),
        "base_road_choices": sum("ROAD" in a["type"] for a, _, _ in rows),
        "deep_road_choices": sum("ROAD" in b["type"] for _, b, _ in rows),
    }
    for label, offset in (("base", 0), ("deep", 1)):
        result[f"{label}_mean_seconds"] = (
            statistics.mean(row[offset]["seconds"] for row in rows) if n else None
        )
        result[f"{label}_mean_simulations"] = (
            statistics.mean(row[offset]["simulations"] for row in rows) if n else None
        )
        result[f"{label}_mean_root_value"] = (
            statistics.mean(row[offset]["value"] for row in rows) if n else None
        )
    return result


class RoadBudgetPlayer(NetPlayer):
    """Use the deeper budget only for the benchmark's public road condition."""

    def __init__(self, color, path, base, deep):
        super().__init__(color, self.Params(path=path, simulations=base))
        self.base = base
        self.deep = deep

    def decide(self, game, playable_actions):
        self.simulations = self.deep if contested(game.state) else self.base
        return super().decide(game, playable_actions)


def road_player(color, path, base, deep):
    return RoadBudgetPlayer(color, path, base, deep)


def fixed_player(color, path, simulations):
    return NetPlayer(color, NetPlayer.Params(path=path, simulations=simulations))


def paired_games(net_path, base, deep, control, games, seed, workers):
    a = partial(road_player, path=net_path, base=base, deep=deep)
    b = partial(fixed_player, path=net_path, simulations=control)
    result = match(a, b, games=games, seed=seed, workers=workers)
    adaptive_ms, control_ms = result.ms_per_move()
    adaptive_seconds = sum(game.a_seconds for game in result.games)
    control_seconds = sum(game.b_seconds for game in result.games)
    ratio = adaptive_seconds / control_seconds if control_seconds else None
    return {
        "games": result.n,
        "score": result.score,
        "margin_95": result.margin if math.isfinite(result.margin) else None,
        "adaptive_ms_per_move": adaptive_ms,
        "control_ms_per_move": control_ms,
        "adaptive_total_thinking_seconds": adaptive_seconds,
        "control_total_thinking_seconds": control_seconds,
        "total_compute_ratio": ratio,
        "compute_matched_within_5_percent": (
            abs(ratio - 1) <= 0.05 if ratio is not None else False
        ),
        "base_simulations": base,
        "deep_simulations": deep,
        "control_simulations": control,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", help="network .npz; use the same net for both policies")
    parser.add_argument("--index", default="data/human/index.jsonl")
    parser.add_argument("--captures", default="captures")
    parser.add_argument("--last", type=int, default=600)
    parser.add_argument("--max-positions", type=int, default=30)
    parser.add_argument("--per-game", type=int, default=2)
    parser.add_argument("--base", type=int, default=800)
    parser.add_argument("--deep", type=int, default=1600)
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--games", type=int, default=0, help="paired simulated games")
    parser.add_argument("--control-sims", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--json", help="save aggregate results, never capture details")
    args = parser.parse_args(argv)
    if args.base < 1 or args.deep <= args.base:
        parser.error("require 1 <= --base < --deep")
    if (
        args.games < 0
        or args.games % 2
        or args.max_positions < 0
        or args.per_game < 1
        or args.last < 0
    ):
        parser.error("games must be even; counts must be nonnegative")
    if not args.max_positions and not args.games:
        parser.error("request positions, games, or both")
    if args.games and not args.control_sims:
        parser.error("--games requires --control-sims for a compute-matched control")
    if args.control_sims is not None and args.control_sims < 1:
        parser.error("--control-sims must be positive")
    if args.time_limit is not None and args.time_limit <= 0:
        parser.error("--time-limit must be positive")
    result = {}
    if args.max_positions:
        result["decisions"] = audit(
            args.net,
            args.index,
            args.captures,
            args.last,
            args.max_positions,
            args.per_game,
            args.base,
            args.deep,
            args.time_limit,
        )
    if args.games:
        result["paired_games"] = paired_games(
            args.net,
            args.base,
            args.deep,
            args.control_sims,
            args.games,
            args.seed,
            args.workers,
        )
    output = json.dumps(result, indent=2)
    print(output)
    if args.json:
        Path(args.json).write_text(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
