"""Self-play from real games' positions (starts.py) and the auxiliary
targets self-play records for training (selfplay.aux_targets, train.py)."""

import gzip
import json
import shutil

import numpy as np
import pytest

from catanatron.colonist import graph as G
from catanatron.colonist import starts as S
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.models.enums import ActionPrompt

from .test_convert import FULL_GAME


@pytest.fixture(scope="module")
def entry():
    return S.start_entry(convert(load_capture(FULL_GAME)).record)


def test_an_entry_keeps_the_game_but_no_names(entry):
    assert set(entry) == {"map", "seats", "rules", "robber", "log", "cuts"}
    assert entry["cuts"] and entry["cuts"] == sorted(entry["cuts"])
    # hidden draws are resolved, so a replay never needs the rest of the game
    assert all(
        result is not None
        for action, result in entry["log"]
        if action[1] == "BUY_DEVELOPMENT_CARD"
    )


def test_a_start_is_a_turn_start_in_range_and_fixed_by_its_seed(entry):
    for seed in range(5):
        game = S.start_game(entry, seed)
        state = game.state
        assert S.MIN_TURN <= state.num_turns <= S.MAX_TURN
        assert game.winning_color() is None
        assert state.current_prompt == ActionPrompt.PLAY_TURN
        assert not state.is_initial_build_phase and state.random is not None
    a, b = S.start_game(entry, 7), S.start_game(entry, 7)
    assert a.state.num_turns == b.state.num_turns
    assert a.state.development_listdeck == b.state.development_listdeck


def test_build_reads_only_the_training_split(tmp_path, monkeypatch):
    captures = tmp_path / "captures" / "acct"
    captures.mkdir(parents=True)
    shutil.copy(FULL_GAME, captures / "a.json")
    shutil.copy(FULL_GAME, captures / "b.json")
    dataset = tmp_path / "human"
    dataset.mkdir()
    rows = [
        {"capture": "a.json", "account": "acct", "status": "ok", "split": "train"},
        {"capture": "b.json", "account": "acct", "status": "ok", "split": "holdout"},
        {"capture": "c.json", "account": "acct", "status": "short"},
    ]
    (dataset / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv("COLONIST_CAPTURES", str(tmp_path / "captures"))
    out = str(dataset / "starts.jsonl.gz")
    games, cuts = S.build(str(dataset), out, log=lambda _: None)
    assert games == 1 and cuts > 0
    with gzip.open(out, "rt") as stream:
        assert "name" not in stream.read()
    assert len(S.load(out)) == 1


def test_selfplay_from_human_starts_records_aux_targets(tmp_path, entry):
    from catanatron.colonist.selfplay import run_shard

    net_path = str(tmp_path / "net.npz")
    G.random_graph_net(8, 8, 1, seed=1).save(net_path)
    starts = tmp_path / "starts.jsonl.gz"
    with gzip.open(starts, "wt") as stream:
        stream.write(json.dumps(entry) + "\n")
    task = (str(tmp_path / "sp"), net_path, 0, 3, {"simulations": 4})
    path, _, rows, _, _, _ = run_shard(task + ((), 0.0, str(starts), 1.0))
    d = np.load(path)
    assert d["human_start"].all() and rows == len(d["z"])
    assert d["aux"].shape == (rows, G.AUX_TARGETS)
    assert set(np.unique(d["aux"][:, :2])) <= {-1, 0, 1}
    # the winner of a finished game ends with 15 points
    finished = d["z"] != 0
    assert np.allclose(d["aux"][finished & (d["z"] == 1), 2], 1.0)
    plain, _, _, _, _, _ = run_shard(
        (str(tmp_path / "plain"), net_path, 0, 2, {"simulations": 4})
    )
    assert not np.load(plain)["human_start"].any()


def test_aux_head_matches_in_torch_and_numpy_and_is_optional(tmp_path):
    torch = pytest.importorskip("torch")
    from catanatron.colonist.net import Net

    model = G.torch_model(8, 8, 1)
    x = np.random.default_rng(0).random((3, 1297)).astype(np.float32)
    with torch.no_grad():
        lt, vt, at = model(torch.from_numpy(x), aux=True)
    params = {
        k: (model.w[k].detach().numpy(), model.b[k].detach().numpy()) for k in model.w
    }
    l, v, a = G.forward(params, x, 1, aux=True)
    assert np.allclose(at.numpy(), a, atol=1e-5) and a.shape == (3, G.AUX_TARGETS)
    with_aux = G.GraphNet(params, 8, 8, 1)
    with_aux.save(str(tmp_path / "aux.npz"))
    assert "aux2" in Net.load(str(tmp_path / "aux.npz")).params
    plain = G.random_graph_net(8, 8, 1)  # an older net, without the head
    plain.save(str(tmp_path / "plain.npz"))
    assert "aux2" not in Net.load(str(tmp_path / "plain.npz")).params


def test_aux_loss_skips_rows_without_targets():
    torch = pytest.importorskip("torch")
    from catanatron.colonist.train import losses

    model = G.torch_model(8, 8, 1)
    n = 4
    x = torch.rand(n, 1297)
    legal = torch.ones(n, 313, dtype=torch.bool)
    action = torch.zeros(n, dtype=torch.long)
    value = torch.zeros(n)
    aux = torch.full((n, G.AUX_TARGETS), float("nan"))
    *_, none = losses(model, x, legal, action, None, value, aux, 1.0, 0.1)
    assert none.item() == 0
    aux[:2] = 1.0
    total, pl, vl, _, _, some = losses(
        model, x, legal, action, None, value, aux, 1.0, 0.1
    )
    assert some.item() > 0 and torch.isfinite(total)
    assert torch.isclose(total, pl + vl + 0.1 * some)


def test_training_learns_aux_alongside(tmp_path, capsys):
    pytest.importorskip("torch")
    from catanatron.colonist.net import Net
    from catanatron.colonist.selfplay import run_shard
    from catanatron.colonist.train import main

    net_path = str(tmp_path / "net.npz")
    G.random_graph_net(8, 8, 1, seed=1).save(net_path)
    run_shard((str(tmp_path / "sp"), net_path, 0, 4, {"simulations": 4}))
    args = [str(tmp_path / "sp"), "--out", str(tmp_path / "g"), "--init", net_path]
    args += [
        "--epochs",
        "1",
        "--batch",
        "32",
        "--device",
        "cpu",
        "--val-fraction",
        "0.5",
    ]
    assert main(args) == 0
    assert " aux " in capsys.readouterr().out
    assert "aux2" in Net.load(str(tmp_path / "g" / "net.npz")).params
