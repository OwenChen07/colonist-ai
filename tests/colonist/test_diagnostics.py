from catanatron.colonist.diagnostics import (
    Diagnostics,
    empty_counts,
    record_rows,
    summary,
)
from catanatron.colonist.record import record_from_game
from catanatron.colonist.rules import new_game
from catanatron.models.player import Color
from catanatron.registry import REGISTRY


def test_simulated_game_and_replay_have_the_same_diagnostics():
    import catanatron.players  # noqa: F401

    game = new_game(
        [REGISTRY.build("W", Color.RED), REGISTRY.build("R", Color.BLUE)], seed=7
    )
    observer = Diagnostics()
    game.play(accumulators=[observer])
    report, rows = record_rows(record_from_game(game))

    assert report.ok
    keys = [*empty_counts(), "final"]
    assert {row["color"]: {k: row[k] for k in keys} for row in rows} == {
        color.value: counts for color, counts in observer.counts.items()
    }


def test_final_state_matches_the_end_of_the_game():
    import catanatron.players  # noqa: F401

    game = new_game(
        [REGISTRY.build("W", Color.RED), REGISTRY.build("R", Color.BLUE)], seed=7
    )
    observer = Diagnostics()
    winner = game.play(accumulators=[observer])
    final = {color: row["final"] for color, row in observer.counts.items()}

    assert final[winner]["vp"] >= 10
    rolls = sum(
        1 for r in game.state.action_records if r.action.action_type.value == "ROLL"
    )
    assert {f["rolls"] for f in final.values()} == {rolls}
    assert sum(f["longest_road"] for f in final.values()) <= 1
    for color, f in final.items():
        assert f["cities"] + f["settlements"] >= 2
        assert f["roads"] >= f["road_length"]
        assert "VICTORY_POINT" not in f["dev_played"]
    assert summary(observer.rows(winner))["seats"] == 2


def test_summary_reports_event_and_no_event_win_rates():
    rows = [
        {"score": 1, **empty_counts(), "roads_one_short": 2},
        {"score": 0, **empty_counts()},
    ]
    row = summary(rows)["roads_one_short"]
    assert row["events"] == 2
    assert row["seats"] == 1
    assert row["wins_per_scored_seat"] == 1
    assert row["wins_without_event"] == 0
