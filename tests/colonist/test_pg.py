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


def test_pg_training_runs_and_starts_at_ratio_one(tmp_path):
    import torch

    from catanatron.colonist import graph as G
    from catanatron.colonist import train

    net = G.random_graph_net(8, 8, 1, seed=1)
    net_path = str(tmp_path / "net.npz")
    net.save(net_path)
    games, _ = pg.play_games(net, range(4), [], self_rate=1.0)
    (tmp_path / "data").mkdir()
    shard = str(tmp_path / "data" / "shard_0.npz")
    pg.write_shard(shard, games)

    static, split, _, _ = train.load_split([shard], 0.0, pg=True)
    data = train.Data(static, split, "cpu")
    assert np.allclose(split["value"], np.load(shard)["ret"])
    model = train.torch_model_of(net_path).eval()
    idx = torch.arange(data.n)
    x, legal, action, _, ret, _ = data.batch(idx)
    old_logp, adv = data.pg_batch(idx)
    with torch.no_grad():
        _, _, _, _, kl, clipped = train.pg_losses(
            model, model, x, legal, action, ret, old_logp, adv
        )
    # the net that played the moves: ratio 1 and no distance to itself
    assert float(clipped) == 0 and abs(float(kl)) < 1e-5

    out = str(tmp_path / "out")
    args = [str(tmp_path / "data"), "--out", out, "--init", net_path, "--pg"]
    assert train.main(args + ["--epochs", "1", "--device", "cpu", "--batch", "64"]) == 0
    assert (tmp_path / "out" / "last.npz").exists()
