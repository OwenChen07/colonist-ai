"""Promoted checkpoints are installed safely for cloud ranked play."""

import json

import numpy as np
import pytest

from catanatron.colonist.ranked_runner import (
    install_promoted_model,
    promoted_checkpoint,
)


def test_promoted_checkpoint_is_atomic_and_only_changes_for_new_content(tmp_path):
    run = tmp_path / "training"
    nets = run / "nets"
    nets.mkdir(parents=True)
    np.savez(nets / "round_005.npz", weights=np.array([5]))
    (run / "state.json").write_text(json.dumps({"best_round": 5}))
    round_number, source = promoted_checkpoint(run)
    assert round_number == 5
    destination = tmp_path / "ranked" / "model.npz"
    assert install_promoted_model(source, destination)
    assert not install_promoted_model(source, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [5]

    np.savez(nets / "round_006.npz", weights=np.array([6]))
    (run / "state.json").write_text(json.dumps({"best_round": 6}))
    round_number, source = promoted_checkpoint(run)
    assert round_number == 6
    assert install_promoted_model(source, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [6]


def test_invalid_promoted_checkpoint_keeps_current_model(tmp_path):
    good = tmp_path / "good.npz"
    bad = tmp_path / "bad.npz"
    destination = tmp_path / "model.npz"
    np.savez(good, weights=np.array([5]))
    bad.write_bytes(b"incomplete checkpoint")
    install_promoted_model(good, destination)
    with pytest.raises(ValueError):
        install_promoted_model(bad, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [5]


def test_each_verified_game_is_listed_with_its_net(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import record_game, round_label

    net = tmp_path / "model.npz"
    net.write_bytes(b"weights")
    conversion = SimpleNamespace(
        ended="win",
        record={
            "winner": "RED",
            "players": {"RED": {"you": True}, "BLUE": {"you": False}},
        },
    )
    label = round_label(tmp_path / "runs" / "sp1", 8)
    assert label == "sp1 round 8"
    record_game(tmp_path, "captures/bridge-1.json", conversion, net, label, 8, 800)
    (row,) = [json.loads(line) for line in open(tmp_path / "games.jsonl")]
    assert row["capture"] == "bridge-1.json" and row["round"] == 8
    assert row["bot_color"] == "RED" and row["winner"] == "RED"
    assert row["label"] == label and len(row["net_sha256"]) == 16
