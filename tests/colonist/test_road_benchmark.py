import json
from types import SimpleNamespace

import pytest

from catanatron.colonist import road_benchmark as road
from catanatron.colonist.net import random_net
from catanatron.colonist.rules import new_game
from catanatron.models.enums import ActionPrompt
from catanatron.models.player import Color, RandomPlayer


def state(roads=(5, 6), visible=(10, 9), prompt=ActionPrompt.PLAY_TURN):
    return SimpleNamespace(
        colors=(Color.RED, Color.BLUE),
        color_to_index={Color.RED: 0, Color.BLUE: 1},
        current_prompt=prompt,
        player_state={
            "P0_LONGEST_ROAD_LENGTH": roads[0],
            "P1_LONGEST_ROAD_LENGTH": roads[1],
            "P0_VICTORY_POINTS": visible[0],
            "P1_VICTORY_POINTS": visible[1],
            "P0_ACTUAL_VICTORY_POINTS": 14,
            "P1_ACTUAL_VICTORY_POINTS": 14,
        },
    )


def test_contested_uses_public_points_and_road_thresholds():
    assert road.contested(state())
    assert not road.contested(state(roads=(4, 6)))
    assert not road.contested(state(roads=(5, 8)))
    assert not road.contested(state(visible=(9, 9)))
    assert not road.contested(state(prompt=ActionPrompt.BUILD_INITIAL_SETTLEMENT))


def test_index_uses_verified_training_captures_only(tmp_path):
    index = tmp_path / "index.jsonl"
    rows = [
        {
            "account": "private-account",
            "capture": "a.json",
            "status": "ok",
            "split": "train",
        },
        {
            "account": "private-account",
            "capture": "b.json",
            "status": "ok",
            "split": "holdout",
        },
        {
            "account": "private-account",
            "capture": "c.json",
            "status": "error",
            "split": "train",
        },
        {
            "account": "private-account",
            "capture": "d.json",
            "status": "ok",
            "split": "train",
        },
    ]
    index.write_text("\n".join(json.dumps(row) for row in rows + [rows[0]]) + "\n")
    found = list(road.indexed_captures(index, tmp_path, last=1))
    assert found == [tmp_path / "private-account" / "d.json"]


def test_search_audit_runs_same_redeal_seed_at_both_budgets():
    game = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=12)
    net = random_net(hidden=(16, 16), seed=12)
    low = road.search_once(game, net, simulations=4, seed=99)
    high = road.search_once(game, net, simulations=8, seed=99)
    assert low["simulations"] <= high["simulations"]
    assert low["seconds"] >= 0 and high["seconds"] >= 0
    assert game.state.num_turns == 0


def test_paired_games_require_explicit_control():
    with pytest.raises(SystemExit):
        road.main(["net.npz", "--max-positions", "0", "--games", "2"])
