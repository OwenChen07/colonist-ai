import json

import numpy as np
import pytest

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.datagen import load
from catanatron.colonist.human_data import export_capture

from .test_convert import FIXTURE, FULL_GAME


def test_full_capture_exports_training_rows_without_identity(tmp_path):
    path, used, total = export_capture(FULL_GAME, str(tmp_path), keep=40)
    assert used == 40 and total >= used
    d = load([path])
    assert d["static"].shape == (1, E.STATIC_SIZE)
    assert d["dynamic"].shape == (40, E.DYNAMIC_SIZE)
    assert d["legal"].shape == (40, E.NUM_ACTIONS)
    assert np.all(d["legal"][np.arange(40), d["action"]])
    assert set(d["z"]) <= {-1, 1}
    assert all(d[k].dtype.kind in "biuf" for k in d)
    again, used, _ = export_capture(FULL_GAME, str(tmp_path), keep=40)
    assert again == path and used == 0


def test_incomplete_capture_cannot_be_training_data(tmp_path):
    with open(FIXTURE) as f:
        doc = json.load(f)
    start = next(i for i, frame in enumerate(load_capture(FIXTURE)) if frame.type == 4)
    doc["messages"] = doc["messages"][: start + 1]
    capture = tmp_path / "incomplete.json"
    capture.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="no completed winner"):
        export_capture(str(capture), str(tmp_path))


def test_resignation_has_a_result_label(tmp_path):
    path, used, _ = export_capture(FIXTURE, str(tmp_path))
    assert used > 0
    assert set(load([path])["z"]) <= {-1, 1}


def test_bot_side_capture_can_supply_value_without_policy(tmp_path):
    path, used, _ = export_capture(FULL_GAME, str(tmp_path), mode="value")
    assert used == 40
    d = load([path])
    assert np.all(d["action"] == -1)
    assert set(d["z"]) <= {-1, 1}


def test_bot_side_capture_can_label_opponent_setup(tmp_path):
    path, used, total = export_capture(
        FULL_GAME, str(tmp_path), mode="opponent_openings"
    )
    assert 2 <= used == total <= 4
    d = load([path])
    assert np.all(d["action"] >= 0)


def test_live_calibration_of_a_net(tmp_path):
    from catanatron.colonist.calibrate import calibrate, describe, load_value_shards
    from catanatron.colonist.net import random_net

    path, used, _ = export_capture(FULL_GAME, str(tmp_path), mode="value")
    assert str(np.load(path)["net"][0]) == ""  # captured before nets were recorded
    parts = load_value_shards([str(tmp_path)])
    result = calibrate(random_net(hidden=(8,)), parts)
    assert result["positions"] == used and result["games"] == 1
    assert 0 <= result["accuracy"] <= 1 and result["mse"] >= 0
    assert "opening" in result and "live value MSE" in describe(result)
    assert load_value_shards([str(tmp_path)], net_sha256="ab" * 8) == []


def test_calibration_reports_hope_when_even_or_behind():
    from catanatron.colonist.calibrate import calibrate, describe

    class Fixed:
        def forward_rows(self, rows, dynamic):
            return None, dynamic[:, 0]

    # Values -0.2 (stated 40%) at turns 5, 25, 70; 0.8 (stated 90%) at turn 30.
    dynamic = np.array([[-0.2], [-0.2], [-0.2], [0.8]], np.float32)
    parts = [
        (
            np.zeros((1, 1), np.float32),
            dynamic,
            np.zeros(4, np.int64),
            np.array([1, -1, 1, -1], np.float32),
            np.array([5, 25, 70, 30], np.int64),
        )
    ]
    hopeful = calibrate(Fixed(), parts)["hopeful"]
    assert hopeful == {"stated": 0.4, "won": 0.5, "n": 2}
    assert "stated 20-60% from turn 20: 40.0%, won 50.0% (2 positions)" in describe(
        calibrate(Fixed(), parts)
    )
