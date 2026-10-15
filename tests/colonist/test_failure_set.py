"""Failure set admits only fully verified positions and keeps them private."""

from types import SimpleNamespace

from catanatron.colonist import failure_set
from catanatron.colonist.failure_set import positions_in_capture, position_flags


def test_verified_capture_yields_bounded_positions():
    positions = positions_in_capture(
        "tests/colonist/fixtures/capture_full_game.json", 3
    )
    assert len(positions) <= 3
    for game, decision, flags in positions:
        assert decision in game.playable_actions
        assert flags
        assert flags == position_flags(game, decision.color)


def test_short_capture_is_not_a_position():
    assert (
        positions_in_capture("tests/colonist/fixtures/capture_short_game.json", 2) == []
    )


def test_rare_tactical_position_replaces_generic_road_position(monkeypatch):
    collector = failure_set._Collector("RED", 1)
    monkeypatch.setattr(
        failure_set,
        "position_flags",
        lambda game, color: (
            {"road_over_available_settlement": {1}}
            if game.state.num_turns == 1
            else {"immediate_win": {2}}
        ),
    )
    decision = SimpleNamespace(color="RED")
    for turn in (1, 2):
        game = SimpleNamespace(state=SimpleNamespace(num_turns=turn))
        game.copy = lambda g=game: g
        collector.step(game, decision)
    assert len(collector.positions) == 1
    assert collector.positions[0][0].state.num_turns == 2
