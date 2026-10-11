"""The human-game training set (human_dataset.py)."""

import json
import shutil

import numpy as np

from catanatron.colonist import human_dataset as H
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert

from .test_convert import FULL_GAME


def test_dead_roads_are_branches_to_no_building():
    # settlement at 1; roads 1-2-3 lead nowhere; 1-4 ends at a city at 4
    roads = {(1, 2), (2, 3), (1, 4)}
    assert H.dead_roads(roads, {1, 4}) == 2
    assert H.dead_roads({(1, 2), (2, 3)}, {1, 3}) == 0  # joins two buildings
    assert H.dead_roads(set(), {1}) == 0


def test_split_is_fixed_by_game_id():
    splits = [H.split_of(seed) for seed in range(2000)]
    assert splits == [H.split_of(seed) for seed in range(2000)]
    assert 0.1 < splits.count("holdout") / 2000 < 0.2


def _captures(tmp_path):
    folder = tmp_path / "captures" / "acct"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(FULL_GAME, folder / "bridge-1.json")
    return [str(folder / "bridge-1.json")]


def test_build_exports_a_finished_game_and_its_stats(tmp_path):
    out = tmp_path / "human"
    counts = H.build(_captures(tmp_path), out=str(out), log=lambda _: None)
    assert sum(counts.values()) == 1
    (row,) = [json.loads(l) for l in open(out / "index.jsonl")]
    assert row["status"] == "ok" and row["split"] in ("train", "holdout")
    assert row["rolls"] >= 20 and isinstance(row["bot_won"], bool)
    for side in ("bot", "human"):
        stats = row[side]
        assert stats["roads"] >= 2 and 0 <= stats["dead_roads"] <= stats["roads"]
        assert stats["settlements"] + stats["cities"] >= 2
    value = list((out / row["split"] / "value").glob("*.npz"))
    assert len(value) == 1 and set(np.load(value[0])["z"]) <= {-1, 1}
    # A second run skips what the index already has.
    assert H.build(_captures(tmp_path), out=str(out), log=lambda _: None) == {}


def test_games_against_our_own_bots_are_left_out(tmp_path):
    record = convert(load_capture(FULL_GAME)).record
    them = [p["name"] for p in record["players"].values() if not p["you"]][0]
    out = tmp_path / "human"
    H.build(_captures(tmp_path), out=str(out), own_names=[them], log=lambda _: None)
    (row,) = [json.loads(l) for l in open(out / "index.jsonl")]
    assert row["status"] == "own_bot"
    assert not (out / "train").exists() and not (out / "holdout").exists()


def test_short_games_are_left_out(tmp_path):
    out = tmp_path / "human"
    H.build(_captures(tmp_path), out=str(out), min_rolls=10_000, log=lambda _: None)
    (row,) = [json.loads(l) for l in open(out / "index.jsonl")]
    assert row["status"] == "short"


def test_opponent_moves_only_without_hidden_cards(tmp_path):
    from catanatron.colonist.human_data import export_capture
    from catanatron.colonist.replay import replay
    from catanatron.state_functions import player_num_dev_cards

    path, used, total = export_capture(
        FULL_GAME, str(tmp_path), keep=10_000, mode="opponent_known"
    )
    assert used == total > 0
    # Count by hand: the opponent's real decisions while holding no dev card.
    record = convert(load_capture(FULL_GAME)).record
    them = [c for c, p in record["players"].items() if not p["you"]][0]
    seen = []

    class Count:
        def before(self, game):
            pass

        def after(self, game):
            pass

        def step(self, game, decision):
            color = game.state.current_color()
            if color.value == them and len(game.playable_actions) > 1:
                seen.append(player_num_dev_cards(game.state, color) == 0)

    replay(record, observer=Count())
    assert total == sum(seen) <= len(seen)
