import random

import numpy as np
import pytest

from catanatron.colonist.mcts import Search
from catanatron.colonist.selfplay import run
from catanatron.models.enums import ActionPrompt


def test_extra_noise_applies_only_to_initial_settlements():
    initial = ActionPrompt.BUILD_INITIAL_SETTLEMENT
    road = ActionPrompt.BUILD_INITIAL_ROAD
    turn = ActionPrompt.PLAY_TURN
    baseline = Search(None, rng=random.Random(19)).root_gumbel(8, initial)
    setup = Search(None, rng=random.Random(19), setup_noise_scale=2)
    np.testing.assert_allclose(setup.root_gumbel(8, initial), baseline * 2)
    np.testing.assert_allclose(
        Search(None, rng=random.Random(19), setup_noise_scale=2).root_gumbel(8, road),
        baseline,
    )
    np.testing.assert_allclose(
        Search(None, rng=random.Random(19), setup_noise_scale=2).root_gumbel(8, turn),
        baseline,
    )
    assert (
        not Search(None, noise=False, setup_noise_scale=2).root_gumbel(8, initial).any()
    )


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_setup_noise_scale_must_be_positive_and_finite(scale):
    with pytest.raises(ValueError, match="setup_noise_scale"):
        Search(None, setup_noise_scale=scale)


def test_selfplay_resume_rejects_a_changed_setup_scale(tmp_path):
    run("unused.npz", str(tmp_path), games=0, setup_noise_scale=1.8, log=lambda _: None)
    with pytest.raises(ValueError, match="cannot resume"):
        run(
            "unused.npz",
            str(tmp_path),
            games=0,
            setup_noise_scale=2,
            log=lambda _: None,
        )
