"""The measuring stick: how often does bot A beat bot B? (PLAN.md, Phase 1)

    python -m catanatron.colonist.evaluate AB:2 F --games 1000
    python -m catanatron.colonist.evaluate NEW OLD --sprt        # promotion test

Every board is played twice with the seats swapped (a pair), so neither bot
gets the better board or the first move. Games run in parallel, one process
per worker, and a game is a pure function of its seed: the same command gives
the same games on any run.

The margin comes from the spread of pair scores (0, 0.5 or 1 for A per pair),
which accounts for the two games of a pair sharing a board. A game that hits
the turn limit with no winner counts half to each bot.

--sprt runs a sequential probability ratio test of "A wins 50%" against
"A wins 54%" (so its midpoint, 52%, is the promotion threshold), with 5% error
rates both ways, stopping as soon as either is accepted or at --max-games.
"""

import argparse
import math
import multiprocessing
import os
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from catanatron.models.player import Color

COLORS = (Color.RED, Color.BLUE)  # A plays RED, B plays BLUE; seats come from the pair


@dataclass(frozen=True)
class GameResult:
    seed: int
    a_first: bool  # did A have the first seat
    score: float  # for A: 1 win, 0 loss, 0.5 no winner
    turns: int
    # Thinking time, over the decisions with more than one legal move. Not
    # part of equality: a rerun gives the same games, not the same timings.
    a_seconds: float = field(default=0.0, compare=False)
    a_moves: int = field(default=0, compare=False)
    b_seconds: float = field(default=0.0, compare=False)
    b_moves: int = field(default=0, compare=False)


@dataclass
class MatchResult:
    a: str
    b: str
    games: List[GameResult] = field(default_factory=list)
    seconds: float = 0.0
    decision: Optional[str] = None  # SPRT: "promote", "keep", or None if undecided

    @property
    def n(self):
        return len(self.games)

    @property
    def score(self):
        return sum(g.score for g in self.games) / self.n if self.n else float("nan")

    @property
    def first_seat(self):
        """How often the first seat won, whoever sat there."""
        firsts = [g.score if g.a_first else 1 - g.score for g in self.games]
        return sum(firsts) / len(firsts) if firsts else float("nan")

    def pair_scores(self):
        by_seed = {}
        for g in self.games:
            by_seed.setdefault(g.seed, []).append(g.score)
        return [sum(s) / 2 for s in by_seed.values() if len(s) == 2]

    @property
    def margin(self):
        """95% margin of error on the score, from the pairs."""
        pairs = self.pair_scores()
        if len(pairs) < 2:
            return float("nan")
        mean = sum(pairs) / len(pairs)
        var = sum((p - mean) ** 2 for p in pairs) / (len(pairs) - 1)
        return 1.96 * math.sqrt(var / len(pairs))

    def summary(self):
        wins = sum(g.score == 1 for g in self.games)
        losses = sum(g.score == 0 for g in self.games)
        draws = self.n - wins - losses
        line = (
            f"{self.a} vs {self.b}: {self.score:.1%} ± {self.margin:.1%} for {self.a} "
            f"({wins} wins, {losses} losses, {draws} no winner; {self.n} games, "
            f"{self.seconds:.0f} s; first seat won {self.first_seat:.1%})"
        )
        a_ms, b_ms = self.ms_per_move()
        if a_ms is not None:
            line += f"; thinking {a_ms:.1f} vs {b_ms:.1f} ms per move"
        if self.decision:
            line += f" -> {self.decision}"
        return line

    def ms_per_move(self):
        """Average thinking time per real decision, for A and for B."""
        am = sum(g.a_moves for g in self.games)
        bm = sum(g.b_moves for g in self.games)
        if not am or not bm:
            return None, None
        a = 1000 * sum(g.a_seconds for g in self.games) / am
        b = 1000 * sum(g.b_seconds for g in self.games) / bm
        return a, b


# ----- one game -----
def play_game(task):
    """Runs in a worker. A plays RED, B plays BLUE; `a_first` puts A in the
    first seat. Everything random comes from the seed."""
    spec_a, spec_b, seed, a_first, rules = task
    from catanatron.colonist.rules import new_game
    import catanatron.players  # noqa: F401  (registers the bots)
    from catanatron.registry import REGISTRY

    stream = seed * 2 + int(a_first)
    random.seed(stream)  # for bots that draw from the global stream
    a = _build(spec_a, COLORS[0])
    b = _build(spec_b, COLORS[1])
    game = new_game([a, b], seed=seed, **rules)
    _seat(game, [a, b] if a_first else [b, a])
    # The board and dev card deck are dealt from `seed`, so both games of a
    # pair share them. From here on (dice, steals, random bots) each game gets
    # its own stream; otherwise two random bots would replay one game mirrored
    # and an identical-bots check would always say exactly 50%.
    game.random.seed(stream)
    clock_a, clock_b = _clock(a), _clock(b)
    winner = game.play()
    score = 0.5 if winner is None else float(winner == COLORS[0])
    return GameResult(seed, a_first, score, game.state.num_turns, *clock_a, *clock_b)


def _clock(player):
    """Times the player's real decisions. Returns a list that fills in as
    [seconds, moves]."""
    totals = [0.0, 0]
    decide = player.decide

    def timed(game, playable_actions):
        if len(playable_actions) < 2:
            return decide(game, playable_actions)
        start = time.perf_counter()
        try:
            return decide(game, playable_actions)
        finally:
            totals[0] += time.perf_counter() - start
            totals[1] += 1

    player.decide = timed
    return totals


def _build(spec, color):
    """A registry spec ("AB:2"), or a callable taking a color (for bots
    configured in code, such as AlphaBeta with tuned weights)."""
    if callable(spec):
        return spec(color)
    from catanatron.registry import REGISTRY

    return REGISTRY.build(spec, color)


def _seat(game, players):
    """Put the players in this seat order (Game seats them randomly)."""
    from catanatron.models.actions import generate_playable_actions

    s = game.state
    s.players = list(players)
    s.colors = tuple(p.color for p in players)
    s.color_to_index = {c: i for i, c in enumerate(s.colors)}
    game.playable_actions = generate_playable_actions(s)


# ----- matches -----
def tasks_for(spec_a, spec_b, seeds, rules):
    for seed in seeds:
        yield (spec_a, spec_b, seed, True, rules)
        yield (spec_a, spec_b, seed, False, rules)


def match(
    spec_a,
    spec_b,
    games=1000,
    seed=0,
    workers=None,
    rules=None,
    progress: Optional[Callable[[MatchResult], None]] = None,
) -> MatchResult:
    """`games` games (rounded up to whole pairs) on boards seed, seed+1, ..."""
    pairs = (games + 1) // 2
    result = MatchResult(_name(spec_a), _name(spec_b))
    start = time.time()
    tasks = tasks_for(spec_a, spec_b, range(seed, seed + pairs), rules or {})
    for game in _run(tasks, workers):
        result.games.append(game)
        if progress and result.n % 100 == 0:
            result.seconds = time.time() - start
            progress(result)
    result.seconds = time.time() - start
    return result


def sprt(
    spec_a,
    spec_b,
    seed=0,
    workers=None,
    rules=None,
    p0=0.50,
    p1=0.54,
    alpha=0.05,
    beta=0.05,
    max_games=2000,
    batch_pairs=50,
    progress=None,
) -> MatchResult:
    """Promotion test: does A beat B at p1 or better, rather than p0?"""
    lower, upper = math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)
    result = MatchResult(_name(spec_a), _name(spec_b))
    start, next_seed = time.time(), seed
    while result.n < max_games:
        n_pairs = min(batch_pairs, (max_games - result.n + 1) // 2)
        seeds = range(next_seed, next_seed + n_pairs)
        next_seed += n_pairs
        result.games.extend(
            _run(tasks_for(spec_a, spec_b, seeds, rules or {}), workers)
        )
        llr = llr_bernoulli(result.games, p0, p1)
        result.seconds = time.time() - start
        if progress:
            progress(result)
        if llr >= upper:
            result.decision = "promote"
            break
        if llr <= lower:
            result.decision = "keep"
            break
    return result


def llr_bernoulli(games: Sequence[GameResult], p0, p1):
    """Log-likelihood ratio of p1 over p0, counting a no-winner game as half a
    win and half a loss."""
    wins = sum(g.score for g in games)
    losses = len(games) - wins
    return wins * math.log(p1 / p0) + losses * math.log((1 - p1) / (1 - p0))


def _run(tasks, workers):
    """Play the tasks, yielding results in task order as they finish."""
    tasks = list(tasks)
    if workers == 1:
        yield from map(play_game, tasks)
        return
    ctx = multiprocessing.get_context("spawn")  # fresh interpreters: no shared state
    with ctx.Pool(workers) as pool:
        yield from pool.imap(play_game, tasks, chunksize=1)


def _name(spec):
    return spec if isinstance(spec, str) else getattr(spec, "__name__", repr(spec))


# ----- command line -----
def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Bots: R random, W weighted random, F value function, AB:<depth> AlphaBeta.",
    )
    parser.add_argument("a", help="bot A, e.g. AB:2")
    parser.add_argument("b", help="bot B, e.g. F")
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0, help="first board seed")
    parser.add_argument("--workers", type=int, default=None, help="default: all cores")
    parser.add_argument(
        "--sprt", action="store_true", help="promotion test (50%% vs 54%%)"
    )
    parser.add_argument("--max-games", type=int, default=2000, help="cap for --sprt")
    parser.add_argument(
        "--plain-dice", action="store_true", help="two plain dice instead of balanced"
    )
    args = parser.parse_args(argv)
    # On SIGTERM, exit through the worker pool's cleanup, so killing a match
    # doesn't leave worker processes running (they slowed later runs badly).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    # One BLAS thread per worker process: the net's small matrix products
    # gain nothing from more, and threads across workers fight for cores.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")
    rules = {"dice": "random"} if args.plain_dice else {}
    show = lambda r: print(f"  {r.n} games: {r.score:.1%} ± {r.margin:.1%}", flush=True)
    if args.sprt:
        result = sprt(
            args.a,
            args.b,
            args.seed,
            args.workers,
            rules,
            max_games=args.max_games,
            progress=show,
        )
    else:
        result = match(args.a, args.b, args.games, args.seed, args.workers, rules, show)
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
