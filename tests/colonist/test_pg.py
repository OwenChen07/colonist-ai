"""The policy-gradient actor (E54): games without search and their shards."""

import numpy as np

from catanatron.colonist import pg
from catanatron.colonist.net import random_net


def test_gae_lambda_one_is_the_result_and_zero_is_one_step():
    values = [0.1, -0.2, 0.4]
    adv, ret = pg.gae(values, 1, lam=1.0)
    assert np.allclose(ret, 1.0)
    assert np.allclose(adv, 1 - np.asarray(values))
    adv, ret = pg.gae(values, -1, lam=0.0)
    assert np.allclose(ret, [-0.2, 0.4, -1.0])


def test_games_are_recorded_and_fixed_by_the_seed(tmp_path):
    learner, other = random_net(hidden=(32, 16)), random_net(hidden=(16,), seed=1)
    games, calls = pg.play_games(learner, range(4), [other], self_rate=0.5)
    assert calls > 0 and all(g.done() for g in games)
    for g in games:
        seats = set(g.seats.values())
        assert len(g.record) == (1 if other in seats else 2)
        assert {r[5] for r in g.rows} <= g.record
    again, _ = pg.play_games(learner, range(4), [other], self_rate=0.5)
    assert [len(g.rows) for g in games] == [len(g.rows) for g in again]

    path = str(tmp_path / "shard.npz")
    pg.write_shard(path, games)
    d = np.load(path)
    n = sum(len(g.rows) for g in games)
    assert len(d["action"]) == len(d["logp"]) == len(d["adv"]) == n
    assert (d["action"] >= 0).all() and (d["logp"] <= 0).all()
    assert np.allclose(d["ret"], d["adv"] + d["vpred"], atol=1e-5)
