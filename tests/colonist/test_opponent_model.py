"""Step 4: a human opponent model in the search, the live flags that turn it
on, and training and scoring it on people's moves."""

import os
import random

import numpy as np
import pytest

from catanatron.colonist import bridge as B
from catanatron.colonist import encode as E
from catanatron.colonist import mcts as M
from catanatron.colonist.graph import random_graph_net
from catanatron.colonist.net import Net, random_net
from catanatron.colonist.rules import new_game
from catanatron.models.player import Color, RandomPlayer

FULL_GAME = os.path.join(
    os.path.dirname(__file__), "fixtures", "capture_full_game.json"
)


def _game(ticks=0, seed=4):
    random.seed(seed)
    game = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed)
    for _ in range(ticks):
        game.play_tick()
    while len(game.playable_actions) < 2:  # a position with a choice
        game.play_tick()
    return game


def _decisions(root):
    """Every evaluated Decision of a finished search."""
    out, stack = [], [root]
    while stack:
        node = stack.pop()
        if isinstance(node, M.Decision) and node.logits is not None:
            out.append(node)
        if isinstance(node, (M.Decision, M.Chance)):
            stack.extend(c for c in node.children if c is not None)
    return out


def _search(game, **kwargs):
    search = M.Search(
        random_net(hidden=(16,), seed=1),
        simulations=96,
        rng=random.Random(5),
        noise=False,
        **kwargs,
    )
    action, root = search.run(game)
    return search, action, root


def test_mix_logits_is_the_log_of_the_mixture():
    main, human = np.array([2.0, 0.0, -1.0]), np.array([-3.0, 1.0, 0.5])
    p = np.exp(main) / np.exp(main).sum()
    q = np.exp(human) / np.exp(human).sum()
    mixed = M.mix_logits(main, human, 0.3)
    assert np.allclose(np.exp(mixed), 0.7 * p + 0.3 * q)
    assert np.allclose(np.exp(M.mix_logits(main, human, 1.0)), q)


@pytest.mark.parametrize("ticks", [0, 25])
def test_mix_zero_is_the_search_without_a_model(ticks):
    game = _game(ticks)
    model = random_net(hidden=(16,), seed=2)
    plain, a, root = _search(game)
    zero, b, root0 = _search(game, opponent_model=model, opponent_mix=0.0)
    assert E.action_key(a) == E.action_key(b)
    assert np.array_equal(root.N, root0.N) and np.array_equal(root.W, root0.W)
    assert plain.evaluations == zero.evaluations and zero.opponent_evaluations == 0
    for x, y in zip(_decisions(root), _decisions(root0)):
        assert np.array_equal(x.logits, y.logits) and x.value == y.value


@pytest.mark.parametrize("ticks", [0, 25])
def test_the_mixture_is_only_at_the_opponents_nodes(ticks):
    game = _game(ticks)
    net = random_net(hidden=(16,), seed=1)
    model = random_net(hidden=(16,), seed=2)
    search, _, root = _search(game, opponent_model=model, opponent_mix=0.5)
    nodes = _decisions(root)
    theirs = [n for n in nodes if n.color != root.color]
    assert theirs and len(theirs) < len(nodes)
    assert search.opponent_evaluations == len(theirs)
    for node in nodes:
        static, dynamic = E.encode(node.game, node.color)
        legal = E.legal_indices(node.actions)
        logits, values = net.forward(static, dynamic[None, :])
        main = logits[0, legal].astype(np.float64)
        # the main net's; float32 batched vs single forward differ by ~1e-6
        assert node.value == pytest.approx(float(values[0]), rel=1e-4, abs=1e-6)
        if node.color == root.color:
            assert np.allclose(node.logits, main)
        else:
            human, _ = model.forward(static, dynamic[None, :])
            expected = M.mix_logits(main, human[0, legal].astype(np.float64), 0.5)
            assert np.allclose(node.logits, expected)


def test_search_rejects_a_mix_without_a_model():
    net = random_net(hidden=(8,))
    with pytest.raises(ValueError):
        M.Search(net, opponent_mix=0.5)
    with pytest.raises(ValueError):
        M.Search(net, opponent_model=net, opponent_mix=1.5)


def _saved(tmp_path, name, net):
    path = str(tmp_path / name)
    net.save(path)
    return path


def _bridge_from_main(monkeypatch, argv):
    made = {}

    async def fake_serve(make_bridge, port=None, info=None):
        made["bridge"], made["info"] = make_bridge(), info

    monkeypatch.setattr(B, "serve_forever", fake_serve)
    assert B.main(argv) == 0
    return made["bridge"], made["info"]


def test_bridge_flags_reach_the_search(tmp_path, monkeypatch):
    net = _saved(tmp_path, "net.npz", random_net(hidden=(8,), seed=1))
    human = _saved(tmp_path, "human.npz", random_graph_net(8, 8, 1, seed=2))
    bridge, info = _bridge_from_main(
        monkeypatch,
        [net, "--simulations", "8", "--save", "", "--opponent-model", human]
        + ["--opponent-mix", "0.4"],
    )
    assert bridge.opponent_mix == 0.4 and bridge.opponent_model is not None
    assert info["opponent_model"] == human and info["opponent_mix"] == 0.4
    assert "opponent model" in B.describe_info(info)

    built = []

    class Recording(M.Search):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(M, "Search", Recording)
    bridge.choose(_game(40))
    (search,) = built
    assert search.opponent_model is bridge.opponent_model
    assert search.opponent_mix == 0.4

    # The opponent's discard guess uses the same mixture.
    game = _game(40)
    color = game.state.current_color()
    legal = E.legal_indices(game.playable_actions)
    main, _ = bridge.net.evaluate(game, color, legal)
    people, _ = bridge.opponent_model.evaluate(game, color, legal)
    assert np.allclose(bridge.opponent_prior(game), 0.6 * main + 0.4 * people)


def test_bridge_is_unchanged_without_the_flags(tmp_path, monkeypatch):
    net = _saved(tmp_path, "net.npz", random_net(hidden=(8,), seed=1))
    bridge, info = _bridge_from_main(monkeypatch, [net, "--save", ""])
    assert bridge.opponent_model is None and bridge.opponent_mix == 0.0
    assert "opponent_model" not in info
    game = _game(40)
    legal = E.legal_indices(game.playable_actions)
    priors, _ = bridge.net.evaluate(game, game.state.current_color(), legal)
    assert np.array_equal(bridge.opponent_prior(game), priors)
    with pytest.raises(SystemExit):
        B.main([net, "--opponent-mix", "0.5"])


def test_ranked_runner_passes_the_flags_through(tmp_path, monkeypatch):
    from catanatron.colonist import ranked_runner as R

    assert R.opponent_args(None, 0.5) == [] == R.opponent_args("m.npz", 0)
    assert R.opponent_args("m.npz", "0.5") == [
        "--opponent-model",
        "m.npz",
        "--opponent-mix",
        "0.5",
    ]
    human = _saved(tmp_path, "human.npz", random_graph_net(8, 8, 1))
    seen = {}

    async def fake_run(args):
        seen["args"] = args

    monkeypatch.setattr(R, "_run", fake_run)
    monkeypatch.setenv("OPPONENT_MODEL", human)
    monkeypatch.setenv("OPPONENT_MIX", "0.25")
    assert R.main(["--net", "x.npz"]) == 0
    args = seen["args"]
    assert R.opponent_args(args.opponent_model, args.opponent_mix) == [
        "--opponent-model",
        human,
        "--opponent-mix",
        "0.25",
    ]
    monkeypatch.delenv("OPPONENT_MODEL")
    monkeypatch.delenv("OPPONENT_MIX")
    assert R.main(["--net", "x.npz"]) == 0
    assert R.opponent_args(seen["args"].opponent_model, seen["args"].opponent_mix) == []


def test_train_and_score_on_peoples_moves(tmp_path, capsys):
    pytest.importorskip("torch")
    from catanatron.colonist import opponent_model as O
    from catanatron.colonist.human_data import export_capture

    data = tmp_path / "opponent"
    _, used, _ = export_capture(FULL_GAME, str(data), 10_000, "opponent_known")
    init = _saved(tmp_path, "main.npz", random_graph_net(8, 8, 1, seed=3))
    out = tmp_path / "human"
    extra = ["--epochs", "3", "--lr", "3e-2", "--val-fraction", "0"]
    assert O.train([str(data)], init, str(out), extra + ["--device", "cpu"]) == 0
    model = Net.load(str(out / "last.npz"))
    assert (model.hidden_size, model.glob, model.layers) == (8, 8, 1)

    scores = O.score(
        O.shard_paths([str(data)]),
        {"main": Net.load(init), "human": model},
        mixes=[0.5],
    )
    assert set(scores) == {"uniform", "main", "human", "mix 0.5"}
    for s in scores.values():
        assert s["rows"] == used
        assert 0 <= s["accuracy"] <= 1 and np.isfinite(s["cross_entropy"])
    # Trained on these very moves, the model predicts them better.
    assert scores["human"]["cross_entropy"] < scores["main"]["cross_entropy"]

    argv = ["score", str(data), "--net", init, "--model", str(out / "last.npz")]
    assert O.main(argv + ["--mix", "0.5"]) == 0
    printed = capsys.readouterr().out
    assert "human" in printed and "mix 0.5" in printed and "uniform" in printed


def test_train_args_keep_policy_focused_defaults():
    from catanatron.colonist.opponent_model import train_args

    args = train_args(["d"], "init.npz", "out", ["--lr", "1e-3"])
    assert args[args.index("--value-weight") + 1] == "0.1"
    assert args.count("--lr") == 1 and args[args.index("--lr") + 1] == "1e-3"
    assert args[args.index("--init") + 1] == "init.npz"


def test_refresh_replaces_the_model_only_when_not_worse(tmp_path):
    pytest.importorskip("torch")
    import json

    from catanatron.colonist import opponent_model as O
    from catanatron.colonist.human_data import export_capture

    data = tmp_path / "opponent"
    export_capture(FULL_GAME, str(data), 10_000, "opponent_known")
    init = _saved(tmp_path, "main.npz", random_graph_net(8, 8, 1, seed=3))
    model = str(tmp_path / "human" / "net.npz")
    os.makedirs(os.path.dirname(model))
    extra = ["--epochs", "3", "--lr", "3e-2", "--val-fraction", "0.5"]
    extra += ["--device", "cpu"]
    # No model yet: the first one is installed.
    first = O.refresh([str(data)], [str(data)], init, model, extra=extra)
    assert first["replaced"] and os.path.exists(model)
    saved = json.load(open(tmp_path / "human" / "refresh.json"))
    assert saved["replaced"] and "new" in saved["scores"]
    # A model in use that predicts the held-out moves far better is kept.
    good = open(model, "rb").read()
    second = O.refresh(
        [str(data)], [str(data)], init, model, tolerance=-10.0, extra=extra
    )
    assert not second["replaced"] and open(model, "rb").read() == good
