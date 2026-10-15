from catanatron.colonist.bonus_tactics import (
    army_race,
    paid_road_facts,
    secure_against_remaining_road_and_settlement,
)
from catanatron.colonist.bonus_audit import audit, capture_counts
from catanatron.colonist.judgment_benchmark import (
    capture_model_matches,
    review_gate,
)
from catanatron.colonist.judgment_policy import (
    eligible_road_flags,
    filtered_root_actions,
)
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import random_net
from catanatron.colonist.rules import new_game
from catanatron.models.board import Board
from catanatron.models.enums import Action, ActionType
from catanatron.models.player import Color, RandomPlayer, SimplePlayer
from catanatron.state import State
from catanatron.state_functions import player_key


def _state_with_road_tie():
    state = State([SimplePlayer(Color.RED), SimplePlayer(Color.BLUE)])
    board = Board()
    board.build_settlement(Color.RED, 0, initial_build_phase=True)
    for edge in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5)):
        board.build_road(Color.RED, edge)
    board.build_settlement(Color.BLUE, 24, initial_build_phase=True)
    for edge in ((24, 25), (25, 26), (26, 27), (27, 28)):
        board.build_road(Color.BLUE, edge)
    state.board = board
    state.is_initial_build_phase = False
    state.discard_limit = 9
    red, blue = player_key(state, Color.RED), player_key(state, Color.BLUE)
    state.player_state[f"{red}_ROADS_AVAILABLE"] = 1
    state.player_state[f"{red}_LONGEST_ROAD_LENGTH"] = 5
    state.player_state[f"{blue}_ROADS_AVAILABLE"] = 1
    state.player_state[f"{blue}_SETTLEMENTS_AVAILABLE"] = 0
    return state


def test_final_opponent_road_can_only_tie_incumbent():
    state = _state_with_road_tie()
    assert state.board.road_color is Color.RED
    assert secure_against_remaining_road_and_settlement(state, Color.RED) is True
    facts = paid_road_facts(state, Action(Color.RED, ActionType.BUILD_ROAD, (0, 5)))
    assert facts["last_piece"]
    assert facts["sites_added"] == 0
    assert facts["bonus_secure_before"] is True
    assert facts["no_immediate_tactical_gain"] is True


def test_candidate_filters_flagged_road_but_keeps_other_moves():
    state = _state_with_road_tie()
    state.current_player_index = state.color_to_index[Color.RED]
    road = Action(Color.RED, ActionType.BUILD_ROAD, (0, 5))
    end = Action(Color.RED, ActionType.END_TURN, None)
    actions = [road, end]
    assert len(eligible_road_flags(state, actions)) == 1
    assert filtered_root_actions(state, actions) == [end]


def test_search_root_filter_with_one_retained_move_returns_that_move():
    game = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=8)
    chosen = game.playable_actions[0]
    search = Search(
        random_net(hidden=(8,), seed=8),
        simulations=4,
        noise=False,
        root_action_filter=lambda game, actions: [actions[0]],
    )
    action, root = search.run(game)
    assert action == chosen
    assert len(root.actions) == 1
    assert game.state.num_turns == 0


def test_one_opponent_road_can_steal_when_only_one_behind():
    state = _state_with_road_tie()
    state.board.build_road(Color.BLUE, (28, 29))
    assert state.board.road_color is Color.RED  # the incumbent keeps a tie
    state.player_state[f"{player_key(state, Color.BLUE)}_ROADS_AVAILABLE"] = 1
    assert secure_against_remaining_road_and_settlement(state, Color.RED) is False
    state.current_player_index = state.color_to_index[Color.RED]
    road = Action(Color.RED, ActionType.BUILD_ROAD, (0, 5))
    end = Action(Color.RED, ActionType.END_TURN, None)
    assert filtered_root_actions(state, [road, end]) == [road, end]


def test_army_tie_requires_one_more_knight_to_steal():
    state = _state_with_road_tie()
    red, blue = player_key(state, Color.RED), player_key(state, Color.BLUE)
    state.player_state[f"{red}_HAS_ARMY"] = True
    state.player_state[f"{red}_PLAYED_KNIGHT"] = 6
    state.player_state[f"{blue}_PLAYED_KNIGHT"] = 6
    ours = army_race(state, Color.RED)
    theirs = army_race(state, Color.BLUE)
    assert ours.mine_to_claim == 0
    assert ours.theirs_to_claim == 1
    assert theirs.mine_to_claim == 1
    assert theirs.theirs_to_claim == 0


def test_unclaimed_army_requires_three_knights():
    state = _state_with_road_tie()
    red = player_key(state, Color.RED)
    state.player_state[f"{red}_PLAYED_KNIGHT"] = 2
    assert army_race(state, Color.RED).mine_to_claim == 1


def test_five_knights_need_two_to_take_six_knight_incumbent():
    state = _state_with_road_tie()
    red, blue = player_key(state, Color.RED), player_key(state, Color.BLUE)
    state.player_state[f"{blue}_HAS_ARMY"] = True
    state.player_state[f"{red}_PLAYED_KNIGHT"] = 5
    state.player_state[f"{blue}_PLAYED_KNIGHT"] = 6
    assert army_race(state, Color.RED).mine_to_claim == 2


def test_audit_only_counts_fully_verified_games():
    verified = "tests/colonist/fixtures/capture_full_game.json"
    counts = capture_counts(verified)
    assert counts["paid_roads"] == 8
    assert counts["no_immediate_tactical_gain"] == 0
    totals = audit([verified, "tests/colonist/fixtures/missing.json"])
    assert totals["verified_games"] == 1
    assert totals["captures_not_verified"] == 1


def test_review_gate_requires_holdout_positions_and_paired_games():
    decisions = {"positions": 30, "baseline_flagged": 4, "candidate_flagged": 0}
    paired = {
        "games": 1000,
        "candidate_score": 0.515,
        "margin_95": 0.025,
        "compute_ratio": 1.02,
    }
    assert review_gate(decisions, paired, "train") == "incomplete"
    assert review_gate(decisions, paired, "holdout") == "eligible_for_promotion_review"
    paired["candidate_score"] = 0.49
    assert review_gate(decisions, paired, "holdout") == "win_rate_not_established"
    paired["candidate_score"] = 1.0
    paired["margin_95"] = 0.0
    assert review_gate(decisions, paired, "holdout") == "eligible_for_promotion_review"
    decisions["time_limit_seconds"] = 5.0
    decisions["live_budget_compute_ratio"] = 1.06
    assert review_gate(decisions, paired, "holdout") == "compute_regression"
    decisions["live_budget_compute_ratio"] = 1.02
    assert review_gate(decisions, paired, "holdout") == "eligible_for_promotion_review"


def test_capture_model_fingerprint_is_a_prefix(tmp_path):
    path = tmp_path / "capture.json"
    path.write_text('{"bot": {"sha256": "c3febf66"}}')
    assert capture_model_matches(path, "c3febf660ac17639") is True
    assert capture_model_matches(path, "123456780ac17639") is False
