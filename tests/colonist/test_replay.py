"""Game records and the Phase 0 replay check, tested on simulated games."""

import copy
import random

import pytest

from catanatron import Color
from catanatron.colonist import record as rec
from catanatron.colonist.replay import ReplayReport, game_winner, main, replay
from catanatron.colonist.rules import new_game
from catanatron.players.value import ValueFunctionPlayer
from catanatron.models.player import RandomPlayer


def finished_game(seed, players=None):
    random.seed(seed)
    players = players or [
        ValueFunctionPlayer(Color.RED),
        ValueFunctionPlayer(Color.BLUE),
    ]
    game = new_game(players, seed=seed)
    game.play()
    return game


@pytest.fixture(scope="module")
def games():
    return [finished_game(seed) for seed in range(4)]


def test_full_information_record_replays(games):
    for game in games:
        report = replay(rec.record_from_game(game))
        assert report.ok, report.summary()
        assert report.winner == game.winning_color().value
        assert report.dice_mode == "balanced"


def test_record_survives_json(games, tmp_path):
    path = tmp_path / "game.json"
    rec.save(rec.record_from_game(games[0]), path)
    report = replay(rec.load(path))
    assert report.ok, report.summary()


def test_hidden_draws_are_resolved(games):
    """From RED's seat, BLUE's draws are hidden; the replay still checks out."""
    for game in games:
        record = rec.record_from_game(game, perspective=Color.RED)
        report = replay(record)
        assert report.ok, report.summary()
        assert report.winner == game.winning_color().value
    assert any(
        e[1] is None
        for g in games
        for e in rec.record_from_game(g, perspective=Color.RED)["log"]
    ), "no game had a hidden draw; pick other seeds"


def test_play_without_purchase_is_reported():
    record = {
        "log": [
            [["BLUE", "BUY_DEVELOPMENT_CARD", None], None],
            [["BLUE", "PLAY_KNIGHT_CARD", None], None],  # same turn: not allowed
        ]
    }
    with pytest.raises(rec.RecordError):
        rec.resolve_hidden_draws(record)


def test_resolution_respects_turns():
    record = {
        "log": [
            [["BLUE", "BUY_DEVELOPMENT_CARD", None], None],
            [["BLUE", "END_TURN", None], None],
            [["BLUE", "BUY_DEVELOPMENT_CARD", None], None],
            [["BLUE", "PLAY_MONOPOLY", "ORE"], None],
            [["BLUE", "END_TURN", None], None],
        ],
        "hidden_vp_cards": {"BLUE": 1},
    }
    log = rec.resolve_hidden_draws(record)
    assert log[0][1] == "MONOPOLY"  # bought on the earlier turn
    assert log[2][1] == "VICTORY_POINT"  # never played, and one VP card was revealed


def test_illegal_move_is_caught(games):
    record = rec.record_from_game(games[0])
    # Swap the first settlement onto a node the second one is already using.
    log = record["log"]
    log[2] = copy.deepcopy(log[0])
    log[2][0][0] = record["seats"][1]
    report = replay(record)
    assert not report.ok
    assert "not legal" in report.errors[0]


def test_card_count_mismatch_is_caught(games):
    record = rec.record_from_game(games[0])
    record["checks"][0]["resources"] += 1
    report = replay(record)
    assert not report.ok
    assert "resources" in report.errors[0]


def test_non_balanced_dice_are_flagged():
    game = new_game(
        [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=9, dice="random"
    )
    random.seed(9)
    for _ in range(400):
        game.play_tick()
    record = rec.record_from_game(game, checks=False)
    record["rules"]["dice"] = "balanced"  # claim balanced dice for plain-dice rolls
    report = replay(record)
    assert report.ok, report.summary()  # the moves are still legal
    assert report.dice_mode == "random"
    assert report.dice_broke_at is not None


def test_game_winner_after_resignation():
    record = {"seats": ["RED", "BLUE"], "resigned": "BLUE"}
    assert game_winner(record, ReplayReport("r")) == "RED"
    assert game_winner(record, ReplayReport("r", winner="BLUE")) == "BLUE"
    assert game_winner({"seats": ["RED", "BLUE"]}, ReplayReport("r")) is None


def test_cli(games, tmp_path, capsys):
    for i, game in enumerate(games[:2]):
        rec.save(
            rec.record_from_game(game, perspective=Color.BLUE), tmp_path / f"g{i}.json"
        )
    assert main([str(tmp_path / "*.json")]) == 0
    assert "2/2 records replayed cleanly" in capsys.readouterr().out
