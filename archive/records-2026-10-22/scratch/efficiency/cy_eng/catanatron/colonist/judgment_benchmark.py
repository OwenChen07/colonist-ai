"""Private-capture and paired-game benchmark for the judgment candidate.

The capture arm includes only complete games that replay exactly. It compares
choices from equal search budgets at positions with a flagged road available;
recorded human-game moves are descriptive, not ground-truth optimal actions.
The paired arm swaps seats on identical seeded boards and measures thinking
time. Output contains aggregate counts only.

    python -m catanatron.colonist.judgment_benchmark MODEL --split holdout
    python -m catanatron.colonist.judgment_benchmark MODEL --split holdout \
        --games 1000 --workers 8
"""

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
from functools import partial
from pathlib import Path

from catanatron.colonist import encode as E
from catanatron.colonist.bonus_audit import indexed_paths
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.evaluate import match
from catanatron.colonist.judgment_policy import (
    JudgmentPlayer,
    LiveStyleNetPlayer,
    eligible_road_flags,
    filtered_root_actions,
)
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.player import Color


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_model_matches(path, model_hash):
    """Match a capture's saved net fingerprint without exposing its contents."""
    with open(path) as stream:
        saved_hash = json.load(stream).get("bot", {}).get("sha256")
    return model_hash.startswith(saved_hash) if saved_hash else None


class _Positions:
    def __init__(self, color, per_game):
        self.color = color
        self.per_game = per_game
        self.by_turn = {}

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color or len(game.playable_actions) < 2:
            return
        turn = game.state.num_turns
        flags = eligible_road_flags(game.state, game.playable_actions)
        if not flags:
            return
        recorded_flagged = E.action_key(decision) in flags
        old = self.by_turn.get(turn)
        if (old is None and len(self.by_turn) < self.per_game) or (
            old is not None and recorded_flagged and not old[2]
        ):
            self.by_turn[turn] = (game.copy(), decision, recorded_flagged)
        elif old is None and recorded_flagged:
            # Reserve scarce sample slots for observed mistakes. The earlier
            # unflagged position can still be found with a larger per-game cap.
            replace = next(
                (t for t, (_, _, flagged) in self.by_turn.items() if not flagged),
                None,
            )
            if replace is not None:
                del self.by_turn[replace]
                self.by_turn[turn] = (game.copy(), decision, True)

    def after(self, game):
        pass


def positions_in_capture(path, per_game):
    """Never expose a position from an incomplete or failed replay."""
    conversion = convert(load_capture(path))
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return []
    record = conversion.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return []
    observer = _Positions(Color[you[0]], per_game)
    report = replay(record, observer=observer)
    return list(observer.by_turn.values()) if report.ok else []


def search_once(game, net, simulations, seed, filtered, time_limit=None):
    root_filter = (
        (lambda copy, actions: filtered_root_actions(copy.state, actions))
        if filtered
        else None
    )
    search = Search(
        net,
        simulations=simulations,
        rng=random.Random(seed),
        noise=False,
        root_action_filter=root_filter,
    )
    start = time.perf_counter()
    action, _ = search.run(game, time_limit=time_limit)
    return E.action_key(action), search.used, time.perf_counter() - start


def decision_benchmark(
    net, paths, simulations, max_positions, per_game, time_limit=None, repeats=1
):
    totals = {
        "captures_scanned": 0,
        "captures_with_positions": 0,
        "positions": 0,
        "trials": 0,
        "recorded_flagged": 0,
        "baseline_flagged": 0,
        "candidate_flagged": 0,
        "decisions_changed": 0,
        "baseline_agrees_with_recorded": 0,
        "candidate_agrees_with_recorded": 0,
        "simulations": simulations,
        "time_limit_seconds": time_limit,
    }
    base_seconds, candidate_seconds, base_used, candidate_used = [], [], [], []
    for path in paths if max_positions else ():
        if totals["positions"] >= max_positions:
            break
        totals["captures_scanned"] += 1
        try:
            positions = positions_in_capture(path, per_game)
        except (OSError, ValueError, KeyError, IndexError):
            continue
        totals["captures_with_positions"] += bool(positions)
        for game, recorded, was_flagged in positions:
            if totals["positions"] >= max_positions:
                break
            flags = eligible_road_flags(game.state, game.playable_actions)
            recorded_key = E.action_key(recorded)
            position = totals["positions"]
            totals["positions"] += 1
            totals["recorded_flagged"] += was_flagged
            for repeat in range(repeats):
                seed = 1701 + 1009 * position + repeat
                base, n_base, t_base = search_once(
                    game, net, simulations, seed, filtered=False, time_limit=time_limit
                )
                candidate, n_candidate, t_candidate = search_once(
                    game, net, simulations, seed, filtered=True, time_limit=time_limit
                )
                totals["trials"] += 1
                totals["baseline_flagged"] += base in flags
                totals["candidate_flagged"] += candidate in flags
                totals["decisions_changed"] += base != candidate
                totals["baseline_agrees_with_recorded"] += base == recorded_key
                totals["candidate_agrees_with_recorded"] += candidate == recorded_key
                base_seconds.append(t_base)
                candidate_seconds.append(t_candidate)
                base_used.append(n_base)
                candidate_used.append(n_candidate)
    for label, seconds, used in (
        ("baseline", base_seconds, base_used),
        ("candidate", candidate_seconds, candidate_used),
    ):
        totals[f"{label}_mean_seconds"] = statistics.mean(seconds) if seconds else None
        totals[f"{label}_mean_simulations"] = statistics.mean(used) if used else None
    totals["live_budget_compute_ratio"] = (
        statistics.mean(candidate_seconds) / statistics.mean(base_seconds)
        if base_seconds
        else None
    )
    return totals


def judgment_player(color, path, simulations):
    return JudgmentPlayer(
        color, JudgmentPlayer.Params(path=path, simulations=simulations)
    )


def baseline_player(color, path, simulations):
    return LiveStyleNetPlayer(
        color, LiveStyleNetPlayer.Params(path=path, simulations=simulations)
    )


def paired_benchmark(path, simulations, games, seed, workers):
    result = match(
        partial(judgment_player, path=path, simulations=simulations),
        partial(baseline_player, path=path, simulations=simulations),
        games=games,
        seed=seed,
        workers=workers,
    )
    candidate_seconds = sum(g.a_seconds for g in result.games)
    baseline_seconds = sum(g.b_seconds for g in result.games)
    return {
        "games": result.n,
        "simulations_per_decision": simulations,
        "candidate_score": result.score,
        "margin_95": result.margin if math.isfinite(result.margin) else None,
        "candidate_thinking_seconds": candidate_seconds,
        "baseline_thinking_seconds": baseline_seconds,
        "compute_ratio": (
            candidate_seconds / baseline_seconds if baseline_seconds else None
        ),
    }


def review_gate(decisions, paired, split):
    """A conservative review gate; it never deploys a model or a policy."""
    if split != "holdout" or paired is None or paired["games"] < 1000:
        return "incomplete"
    if decisions["positions"] < 30 or decisions["baseline_flagged"] == 0:
        return "insufficient_positions"
    if decisions["candidate_flagged"]:
        return "candidate_still_flags"
    margin = paired["margin_95"]
    lower = paired["candidate_score"] - (margin if margin is not None else float("inf"))
    if lower < 0.48:
        return "win_rate_not_established"
    ratio = (
        decisions.get("live_budget_compute_ratio")
        if decisions.get("time_limit_seconds") is not None
        else paired["compute_ratio"]
    )
    if ratio is None or ratio > 1.05:
        return "compute_regression"
    return "eligible_for_promotion_review"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", help="frozen network .npz")
    parser.add_argument("--index", default="data/human/index.jsonl")
    parser.add_argument("--captures", default="captures")
    parser.add_argument("--capture", help="private single-capture smoke test")
    parser.add_argument("--split", choices=("train", "holdout"), default="train")
    parser.add_argument("--last", type=int, default=600)
    parser.add_argument("--max-positions", type=int, default=50)
    parser.add_argument("--per-game", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--simulations", type=int, default=400)
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--games", type=int, default=0)
    parser.add_argument(
        "--paired-simulations",
        type=int,
        help="search budget for paired games (default: --simulations)",
    )
    parser.add_argument("--seed", type=int, default=50300)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if (
        args.simulations < 1
        or (args.paired_simulations is not None and args.paired_simulations < 1)
        or args.max_positions < 0
        or args.games < 0
        or args.repeats < 1
        or (args.time_limit is not None and args.time_limit <= 0)
    ):
        parser.error("simulations must be positive; counts cannot be negative")
    paths = (
        [Path(args.capture)]
        if args.capture
        else indexed_paths(args.index, args.captures, args.split, args.last)
    )
    model_hash_before = sha256_file(args.net)
    index_hash_before = (
        sha256_file(args.index) if not args.capture and args.max_positions > 0 else None
    )
    exact_model = (
        capture_model_matches(args.capture, model_hash_before) if args.capture else None
    )
    decisions = decision_benchmark(
        Net.load(args.net),
        paths,
        args.simulations,
        args.max_positions,
        args.per_game,
        args.time_limit,
        args.repeats,
    )
    paired = (
        paired_benchmark(
            args.net,
            args.paired_simulations or args.simulations,
            args.games,
            args.seed,
            args.workers,
        )
        if args.games
        else None
    )
    source = (
        "single_capture"
        if args.capture
        else "paired_only" if args.max_positions == 0 else "verified_index"
    )
    input_changed = model_hash_before != sha256_file(args.net) or (
        index_hash_before is not None and index_hash_before != sha256_file(args.index)
    )
    print(
        json.dumps(
            {
                "model_sha256": model_hash_before,
                "source": source,
                "index_sha256": index_hash_before,
                "capture_model_matches": exact_model,
                "input_changed_during_run": input_changed,
                "seed": args.seed,
                "split": args.split,
                "decisions": decisions,
                "paired": paired,
                "review_gate": (
                    "input_changed"
                    if input_changed
                    else review_gate(
                        decisions,
                        paired,
                        args.split if source == "verified_index" else source,
                    )
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
