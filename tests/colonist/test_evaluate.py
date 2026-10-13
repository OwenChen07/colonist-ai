import math

import pytest

from catanatron.colonist.evaluate import (
    GameResult,
    MatchResult,
    llr_bernoulli,
    match,
    sprt,
)


def test_pairs_swap_seats_on_the_same_board():
    result = match("W", "R", games=6, seed=10, workers=1)
    assert result.n == 6
    assert sorted((g.seed, g.a_first) for g in result.games) == [
        (s, f) for s in (10, 11, 12) for f in (False, True)
    ]


def test_matches_repeat_exactly():
    first = match("W", "F", games=4, seed=3, workers=1)
    again = match("W", "F", games=4, seed=3, workers=1)
    assert first.games == again.games


def test_thinking_time_is_reported():
    result = match("F", "R", games=2, seed=1, workers=1)
    assert all(g.a_moves > 0 and g.b_moves > 0 for g in result.games)
    a_ms, b_ms = result.ms_per_move()
    assert a_ms > b_ms > 0  # the value function thinks; random doesn't
    assert "ms per move" in result.summary()


def test_workers_give_the_same_games():
    serial = match("W", "R", games=4, seed=5, workers=1)
    parallel = match("W", "R", games=4, seed=5, workers=2)
    assert serial.games == parallel.games


def test_margin_uses_pairs():
    games = [
        GameResult(s, f, x, 100)
        for s, (a, b) in enumerate([(1, 0), (1, 1), (0, 0), (1, 0)])
        for f, x in ((True, a), (False, b))
    ]
    r = MatchResult("A", "B", games)
    pairs = [0.5, 1.0, 0.0, 0.5]
    mean = sum(pairs) / 4
    sd = math.sqrt(sum((p - mean) ** 2 for p in pairs) / 3)
    assert r.pair_scores() == pairs
    assert r.margin == pytest.approx(1.96 * sd / 2)
    assert r.first_seat == pytest.approx(6 / 8)


def test_llr_favors_the_better_hypothesis():
    wins = [GameResult(i, True, 1.0, 1) for i in range(60)]
    losses = [GameResult(i, True, 0.0, 1) for i in range(40)]
    assert llr_bernoulli(wins + losses, 0.5, 0.54) > 0
    assert llr_bernoulli(wins[:40] + losses + losses, 0.5, 0.54) < 0


def test_sprt_stops_early_on_a_clear_difference():
    result = sprt("W", "R", workers=1, max_games=400, batch_pairs=10)
    assert result.decision == "promote"
    assert result.n < 400


def test_sprt_respects_minimum_games(monkeypatch):
    from catanatron.colonist import evaluate

    monkeypatch.setattr(
        evaluate,
        "_run",
        lambda tasks, workers, hosts=None: (
            GameResult(i, True, 1.0, 1) for i, _ in enumerate(tasks)
        ),
    )
    result = sprt("W", "R", workers=1, min_games=1000, max_games=1200)
    assert result.n == 1000
    assert result.decision == "promote"
