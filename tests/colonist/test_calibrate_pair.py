"""The human comparison treats games, rather than turns, as independent."""

import numpy as np

from catanatron.colonist.calibrate import paired_game_comparison


class _Net:
    def __init__(self, values):
        self.values = values

    def forward_rows(self, static, dynamic):
        return None, self.values[: len(dynamic)]


def test_paired_human_comparison_uses_games_as_units():
    parts = []
    for result in (1, -1, 1):
        static = np.zeros((1, 2), np.float32)
        dynamic = np.zeros((10, 2), np.float32)
        parts.append(
            (
                static,
                dynamic,
                np.zeros(10, np.int64),
                np.full(10, result, np.float32),
                np.arange(10),
            )
        )
    new = _Net(np.ones(10))
    old = _Net(np.zeros(10))
    result = paired_game_comparison(new, old, parts)
    assert result["games"] == 3
    assert result["mse"]["improvement"] < 0
    assert result["accuracy"]["improvement"] > 0
    assert result["mse"]["lower"] < result["mse"]["upper"]
