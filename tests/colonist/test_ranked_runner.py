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
