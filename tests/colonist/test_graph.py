"""The board-aware graph network (graph.py)."""

import numpy as np
import pytest

from catanatron.colonist import encode as E
from catanatron.colonist import graph as G
from catanatron.colonist.net import Net


def _inputs(n=4, seed=0):
    return np.random.default_rng(seed).random((n, E.INPUT_SIZE)).astype(np.float32)


def test_every_move_is_scored_once():
    topo = G.topology()
    every = np.concatenate(
        [
            topo["settle"],
            topo["city"],
            topo["road"],
            topo["robber"].ravel(),
            topo["other"],
        ]
    )
    assert sorted(every) == list(range(E.NUM_ACTIONS))
    assert len(topo["other"]) == G.NUM_OTHER


def test_inputs_are_split_without_overlap():
    topo = G.topology()
    used = np.concatenate(
        [topo["tile_in"].ravel(), topo["node_in"].ravel(), topo["edge_in"].ravel()]
    )
    globals_ = np.arange(E.INPUT_SIZE - G.GLOBAL_FEATURES, E.INPUT_SIZE)
    assert sorted(np.concatenate([used, globals_])) == list(range(E.INPUT_SIZE))


def test_a_symmetric_board_gives_symmetric_moves():
    """Rotating or mirroring the board permutes the move scores exactly the
    same way and leaves the value unchanged: the net can't tell boards apart
    by orientation."""
    from catanatron.colonist.symmetry import symmetries

    net = G.random_graph_net(seed=3)
    x = _inputs()
    logits, values = G.forward(net.params, x, net.layers)
    for sym in symmetries():
        l2, v2 = G.forward(net.params, x[:, sym.inputs], net.layers)
        assert np.allclose(l2, logits[:, sym.moves], atol=1e-4)
        assert np.allclose(v2, values, atol=1e-5)


def test_torch_and_numpy_agree():
    torch = pytest.importorskip("torch")
    net = G.random_graph_net(hidden=16, glob=24, layers=2, seed=1)
    model = G.torch_model(hidden=16, glob=24, layers=2)
    with torch.no_grad():
        for k, (w, b) in net.params.items():
            model.w[k].copy_(torch.from_numpy(w))
            model.b[k].copy_(torch.from_numpy(b))
        lt, vt = model(torch.from_numpy(_inputs()))
    l, v = G.forward(net.params, _inputs(), net.layers)
    assert np.allclose(lt.numpy(), l, atol=1e-4)
    assert np.allclose(vt.numpy(), v, atol=1e-5)


def test_saved_graph_net_loads_through_net_load(tmp_path):
    net = G.random_graph_net(hidden=8, glob=8, layers=1, seed=2)
    net.meta = {"meta_epoch": np.asarray(4)}
    path = str(tmp_path / "g.npz")
    net.save(path)
    loaded = Net.load(path)
    assert isinstance(loaded, G.GraphNet) and int(loaded.meta["meta_epoch"]) == 4
    x = _inputs(2)
    st, dy = x[:, : E.STATIC_SIZE], x[:, E.STATIC_SIZE :]
    a = loaded.forward_rows([st[0], st[1]], dy)
    b = G.forward(net.params, x, net.layers)
    assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_graph_net_plays_through_search():
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.rules import new_game
    from catanatron.models.player import Color, Player

    game = new_game([Player(Color.RED), Player(Color.BLUE)], seed=4)
    search = Search(G.random_graph_net(seed=5), simulations=8)
    action, root = search.run(game)
    assert action in game.playable_actions


def test_train_a_graph_net_and_resume_from_it(tmp_path):
    pytest.importorskip("torch")
    from catanatron.colonist.datagen import run_shard
    from catanatron.colonist.train import main

    run_shard((str(tmp_path / "bot"), "W", 0, 4, 10, 0.0))
    args = [str(tmp_path / "bot"), "--out", str(tmp_path / "g1"), "--arch", "graph"]
    args += ["--graph", "8,8,1", "--epochs", "1", "--batch", "32", "--device", "cpu"]
    args += ["--val-fraction", "0.5"]
    assert main(args) == 0
    net = Net.load(str(tmp_path / "g1" / "net.npz"))
    assert isinstance(net, G.GraphNet) and net.layers == 1
    again = [str(tmp_path / "bot"), "--out", str(tmp_path / "g2"), "--epochs", "1"]
    again += ["--init", str(tmp_path / "g1" / "net.npz"), "--batch", "32"]
    again += ["--device", "cpu", "--data-device", "cpu", "--val-fraction", "0.5"]
    assert main(again) == 0
    assert isinstance(Net.load(str(tmp_path / "g2" / "net.npz")), G.GraphNet)
