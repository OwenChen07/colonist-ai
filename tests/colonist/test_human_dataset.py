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


def _hands_log(moves, revealed=None):
    """A record with BLUE's turns as lists of moves ('B' buy, 'K'/'M' plays);
    RED's turns in between are empty. Returns the record and, per BLUE turn,
    the log index after each of its moves (a decision there)."""
    from catanatron.colonist.record import BUY, END_TURN

    plays = {"K": "PLAY_KNIGHT_CARD", "M": "PLAY_MONOPOLY"}
    log, points = [], []
    for turn in moves:
        here = [len(log)]
        for move in turn:
            log.append([["BLUE", BUY if move == "B" else plays[move], None], None])
            here.append(len(log))
        points.append(here)
        log.append([["BLUE", END_TURN, None], None])
        log.append([["RED", END_TURN, None], None])
    record = {"log": log}
    if revealed is not None:
        record["hidden_vp_cards"] = {"BLUE": revealed}
    return record, points


def test_dev_hands_two_kinds_are_ambiguous_until_both_are_bought():
    from collections import Counter

    from catanatron.colonist.human_data import DevHands

    # Buy, wait, buy; play a knight, then a monopoly, on later turns.
    record, at = _hands_log([["B"], [], ["B"], [], ["K"], ["M"]], revealed=0)
    hands = DevHands(record, "BLUE")
    assert hands.at(at[0][0]) == ("none", Counter())
    assert hands.at(at[1][0])[0] == "ambiguous"  # the knight or the monopoly
    both = Counter({"KNIGHT": 1, "MONOPOLY": 1})
    assert hands.at(at[3][0]) == ("exact", both)
    assert hands.at(at[4][1]) == ("exact", Counter({"MONOPOLY": 1}))
    # A card bought this turn can't be played: the knight is the first card.
    record, at = _hands_log([["B"], [], ["B", "K"], ["M"]], revealed=0)
    hands = DevHands(record, "BLUE")
    assert hands.at(at[1][0]) == ("exact", Counter({"KNIGHT": 1}))


def test_dev_hands_same_kind_needs_no_matching():
    from collections import Counter

    from catanatron.colonist.human_data import DevHands

    record, at = _hands_log([["B"], ["B"], [], ["K"], ["K"]], revealed=0)
    hands = DevHands(record, "BLUE")
    assert hands.at(at[1][0]) == ("exact", Counter({"KNIGHT": 1}))
    assert hands.at(at[2][0]) == ("exact", Counter({"KNIGHT": 2}))
    assert hands.at(at[3][1]) == ("exact", Counter({"KNIGHT": 1}))


def test_dev_hands_count_a_victory_point_card_revealed_at_the_end():
    from collections import Counter

    from catanatron.colonist.human_data import DevHands

    moves = [["B"], ["B"], [], ["K"], []]
    record, at = _hands_log(moves, revealed=1)
    hands = DevHands(record, "BLUE")
    assert hands.at(at[2][0]) == (
        "exact",
        Counter({"KNIGHT": 1, "VICTORY_POINT": 1}),
    )
    assert hands.at(at[4][0]) == ("exact", Counter({"VICTORY_POINT": 1}))
    # Without the reveal the unplayed card could be anything.
    hands = DevHands(_hands_log(moves)[0], "BLUE")
    assert hands.at(at[4][0]) == ("unknown", None)
    assert hands.at(at[2][0]) == ("unknown", None)
    # A play with no purchase on an earlier turn: nothing fits.
    record, at = _hands_log([["B", "K"]], revealed=0)
    assert not DevHands(record, "BLUE").ok


def test_opponent_exact_adds_hands_the_replay_gives_right(tmp_path):
    from catanatron.colonist.human_data import export_capture

    stats_known, stats = {}, {}
    _, _, known = export_capture(
        FULL_GAME, str(tmp_path / "k"), 10_000, "opponent_known", stats_known
    )
    _, used, total = export_capture(
        FULL_GAME, str(tmp_path / "e"), 10_000, "opponent_exact", stats
    )
    assert stats_known == {}  # counted only for opponent_exact
    assert used == total == stats.get("none", 0) + stats.get("exact", 0)
    assert stats.get("none", 0) == known and stats.get("mismatch", 0) == 0
    assert sum(stats.values()) >= total


def test_build_indexes_hands_and_backfills_old_rows(tmp_path):
    out = tmp_path / "human"
    H.build(_captures(tmp_path), out=str(out), log=lambda _: None)
    (row,) = [json.loads(l) for l in open(out / "index.jsonl")]
    assert isinstance(row["exported"]["opponent_exact"], int)
    assert row["exported"]["opponent_exact"] >= row["exported"]["opponent"]
    assert list((out / row["split"] / "opponent_exact").glob("*_opponent_exact.npz"))
    # A row indexed before the exact export gets it on the next run.
    old = {k: v for k, v in row.items() if k != "hands"}
    (out / "index.jsonl").write_text(json.dumps(old) + "\n")
    assert H.backfill_exact(_captures(tmp_path), str(out), log=lambda _: None) == 1
    (again,) = [json.loads(l) for l in open(out / "index.jsonl")]
    assert again["hands"] == row["hands"]
    assert H.backfill_exact(_captures(tmp_path), str(out), log=lambda _: None) == 0
    _person_won(out)
    exact = dict(source="opponent_exact", mode="opponent_exact")
    assert H.policy_won(str(out), lambda _: None, folder="policy_won_exact", **exact)
    assert list((out / row["split"] / "policy_won_exact").glob("*.npz"))


def _person_won(out):
    rows = [json.loads(l) for l in open(out / "index.jsonl")]
    for row in rows:
        row["bot_won"] = False
    (out / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows[0]


def test_policy_won_keeps_the_moves_of_games_the_person_won(tmp_path):
    out = tmp_path / "human"
    H.build(_captures(tmp_path), out=str(out), log=lambda _: None)
    (row,) = [json.loads(l) for l in open(out / "index.jsonl")]
    if row["bot_won"]:
        assert H.policy_won(str(out), log=lambda _: None) == 0
    row = _person_won(out)
    assert H.policy_won(str(out), log=lambda _: None) == 1
    (shard,) = (out / row["split"] / "policy_won").glob("*.npz")
    (source,) = (out / row["split"] / "opponent").glob("*.npz")
    copy, original = np.load(shard), np.load(source)
    assert np.isnan(copy["z"]).all() and "v" not in copy.files
    assert (copy["action"] == original["action"]).all()
    assert (copy["action"] >= 0).all()
    assert H.policy_won(str(out), log=lambda _: None) == 0  # already there


def test_training_reads_moves_without_results(tmp_path, capsys):
    import pytest

    torch = pytest.importorskip("torch")
    from catanatron.colonist import graph as G
    from catanatron.colonist.train import losses, main

    out = tmp_path / "human"
    H.build(_captures(tmp_path), out=str(out), log=lambda _: None)
    row = _person_won(out)
    H.policy_won(str(out), log=lambda _: None)

    model = G.torch_model(8, 8, 1)
    x, legal = torch.rand(4, 1297), torch.ones(4, 313, dtype=torch.bool)
    action = torch.zeros(4, dtype=torch.long)
    value = torch.tensor([1.0, -1.0, float("nan"), float("nan")])
    total, pl, vl, *_ = losses(model, x, legal, action, None, value, None, 1.0)
    assert torch.isfinite(total) and vl.item() > 0
    _, pl2, vl2, *_ = losses(
        model, x, legal, action, None, value.fill_(float("nan")), None, 1.0
    )
    assert vl2.item() == 0 and torch.isclose(pl2, pl)

    net = str(tmp_path / "net.npz")
    G.random_graph_net(8, 8, 1, seed=1).save(net)
    folders = [str(out / row["split"] / f) for f in ("policy_won", "value")]
    args = folders + ["--out", str(tmp_path / "t"), "--init", net, "--epochs", "1"]
    args += ["--batch", "16", "--device", "cpu", "--val-fraction", "0"]
    assert main(args) == 0
    (epoch,) = [l for l in capsys.readouterr().out.splitlines() if "epoch 1" in l]
    assert "train policy" in epoch and "nan" not in epoch
