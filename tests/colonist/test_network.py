"""Phase 2: inputs, moves, the network, the search and the training data."""

import glob
import random

import numpy as np
import pytest

from catanatron.colonist import encode as E
from catanatron.colonist.mcts import NetPlayer, Search, outcomes, redeal_hidden_cards
from catanatron.colonist.net import Net, random_net
from catanatron.colonist.rules import new_game
from catanatron.models.enums import DEVELOPMENT_CARDS, VICTORY_POINT, ActionType
from catanatron.models.player import Color, RandomPlayer
from catanatron.players.weighted_random import WeightedRandomPlayer
from catanatron.state_functions import player_key


def _positions(seed, every=1):
    """Positions of a weighted-random game, as copies."""
    random.seed(seed)
    game = new_game(
        [WeightedRandomPlayer(Color.RED), WeightedRandomPlayer(Color.BLUE)], seed=seed
    )
    out = []
    while game.winning_color() is None and game.state.num_turns < 500:
        if len(out) % every == 0:
            out.append(game.copy())
        game.play_tick()
    return out


@pytest.fixture(scope="module")
def positions():
    return _positions(1) + _positions(2)


def test_action_table():
    table = E.action_table()
    assert len(table) == E.NUM_ACTIONS == len(set(table))


def test_every_legal_move_has_an_index(positions):
    for game in positions:
        idx = E.legal_indices(game.playable_actions)
        assert len(set(idx)) == len(idx)
        for a, i in zip(game.playable_actions, idx):
            assert E.action_key(a) == E.action_table()[i]


def test_robber_victim_is_relative():
    from catanatron.models.enums import Action

    coordinate = (0, 0, 0)
    red_robs_blue = Action(Color.RED, ActionType.MOVE_ROBBER, (coordinate, Color.BLUE))
    blue_robs_red = Action(Color.BLUE, ActionType.MOVE_ROBBER, (coordinate, Color.RED))
    assert E.action_index(red_robs_blue) == E.action_index(blue_robs_red)


def test_input_sizes(positions):
    for game in positions[::7]:
        for color in game.state.colors:
            static, dynamic = E.encode(game, color)
            assert static.shape == (E.STATIC_SIZE,)
            assert dynamic.shape == (E.DYNAMIC_SIZE,)
            assert np.isfinite(dynamic).all()


def _set_dev_hand(game, color, cards):
    key = player_key(game.state, color) + "_"
    ps = game.state.player_state
    vps = cards.count(VICTORY_POINT) - ps[key + VICTORY_POINT + "_IN_HAND"]
    ps[key + "ACTUAL_VICTORY_POINTS"] += vps
    for c in DEVELOPMENT_CARDS:
        ps[key + c + "_IN_HAND"] = cards.count(c)


def test_opponents_hidden_cards_do_not_leak(positions):
    """Changing which development cards the opponent holds (not how many)
    must not change what the player sees."""
    for game in positions[::25]:
        me, them = game.state.colors
        a, b = game.copy(), game.copy()
        _set_dev_hand(a, them, ["KNIGHT", "KNIGHT"])
        _set_dev_hand(b, them, [VICTORY_POINT, "MONOPOLY"])
        assert np.array_equal(E.encode(a, me)[1], E.encode(b, me)[1])
        # ...while the owner does see them
        assert not np.array_equal(E.encode(a, them)[1], E.encode(b, them)[1])


def test_redeal_keeps_what_the_viewer_knows(positions):
    rng = random.Random(0)
    for game in positions[::20]:
        me, them = game.state.colors
        g = game.copy()
        _set_dev_hand(g, them, ["KNIGHT", VICTORY_POINT])
        before_all = sorted(g.state.development_listdeck + ["KNIGHT", VICTORY_POINT])
        mine_before = E.encode(g, me)[1]
        redeal_hidden_cards(g, me, rng)
        key = player_key(g.state, them) + "_"
        ps = g.state.player_state
        hand = [c for c in DEVELOPMENT_CARDS for _ in range(ps[key + c + "_IN_HAND"])]
        assert len(hand) == 2
        assert sorted(g.state.development_listdeck + hand) == before_all
        vps = ps[key + "VICTORY_POINTS"] + hand.count(VICTORY_POINT)
        assert ps[key + "ACTUAL_VICTORY_POINTS"] == vps
        assert np.array_equal(E.encode(g, me)[1], mine_before)


def test_redeal_never_ends_the_game():
    """A deal that would give the opponent 15 points can't be the real one."""
    game = _positions(3)[-60]
    me, them = game.state.colors
    key = player_key(game.state, them) + "_"
    ps = game.state.player_state
    _set_dev_hand(game, them, ["KNIGHT", "KNIGHT", "KNIGHT"])
    visible = ps[key + "ACTUAL_VICTORY_POINTS"]
    ps[key + "VICTORY_POINTS"] += 13 - visible  # two short of winning
    ps[key + "ACTUAL_VICTORY_POINTS"] = 13
    game.state.development_listdeck = [VICTORY_POINT] * 5 + ["MONOPOLY"]
    rng = random.Random(0)
    for _ in range(20):
        g = game.copy()
        redeal_hidden_cards(g, me, rng)
        assert g.winning_color() is None
        assert g.state.player_state[key + "ACTUAL_VICTORY_POINTS"] < 15


def test_chance_outcomes_sum_to_one(positions):
    seen = set()
    for game in positions:
        for action in game.playable_actions:
            outs = outcomes(game, action)
            if outs is None:
                continue
            seen.add(action.action_type)
            assert abs(sum(p for p, _ in outs) - 1) < 1e-9
            assert all(p > 0 for p, _ in outs)
    assert ActionType.ROLL in seen and ActionType.MOVE_ROBBER in seen


def test_search_uses_its_budget_and_plays_legal_moves(positions):
    net = random_net(hidden=(32, 32))
    for game in positions[::40]:
        if len(game.playable_actions) < 2:
            continue
        before = game.copy()
        search = Search(net, simulations=24, rng=random.Random(0))
        action, root = search.run(game)
        assert action in root.actions
        assert E.action_key(action) in {E.action_key(a) for a in game.playable_actions}
        assert root.N.sum() >= 23
        # the game searched is left alone
        assert game.state.player_state == before.state.player_state
        assert game.state.num_turns == before.state.num_turns


def test_net_player_finishes_games():
    net = random_net(hidden=(32, 32))
    for sims in (0, 8):
        player = NetPlayer(Color.RED, NetPlayer.Params(simulations=sims), net=net)
        game = new_game([player, RandomPlayer(Color.BLUE)], seed=5)
        game.play()
        assert game.winning_color() is not None or game.state.num_turns >= 1000


def test_net_registered():
    import catanatron.players  # noqa: F401
    from catanatron.registry import REGISTRY

    assert REGISTRY["Z"] is NetPlayer


def test_net_save_and_load(tmp_path, positions):
    net = random_net(hidden=(32, 16))
    path = str(tmp_path / "net.npz")
    net.save(path)
    loaded = Net.load(path)
    game = positions[50]
    color = game.state.current_color()
    legal = E.legal_indices(game.playable_actions)
    p1, v1 = net.evaluate(game, color, legal)
    p2, v2 = loaded.evaluate(game, color, legal)
    assert np.allclose(p1, p2) and v1 == pytest.approx(v2)
    assert p1.sum() == pytest.approx(1)


def test_datagen_shard(tmp_path):
    from catanatron.colonist.datagen import load, run_shard

    run_shard((str(tmp_path), "W", 10, 2, 15, 0.1))
    d = load(glob.glob(str(tmp_path / "*.npz")))
    assert len(d["static"]) == 2
    n = len(d["action"])
    assert 0 < n <= 30
    assert d["dynamic"].shape == (n, E.DYNAMIC_SIZE)
    targets = d["action"] >= 0
    assert d["legal"][np.flatnonzero(targets), d["action"][targets]].all()
    assert set(np.unique(d["z"])) <= {-1, 0, 1}
    # repeatable
    run_shard((str(tmp_path / "again"), "W", 10, 2, 15, 0.1))
    d2 = load(glob.glob(str(tmp_path / "again" / "*.npz")))
    assert np.array_equal(d["dynamic"], d2["dynamic"])


def test_training_round_trip(tmp_path):
    pytest.importorskip("torch")
    from catanatron.colonist.datagen import run_shard
    from catanatron.colonist.train import main

    data = tmp_path / "data"
    run_shard((str(data), "W", 0, 4, 20, 0.0))
    out = tmp_path / "model"
    args = [
        str(data),
        "--out",
        str(out),
        "--hidden",
        "32,32",
        "--batch",
        "16",
        "--val-fraction",
        "0.5",
        "--device",
        "cpu",
    ]
    assert main(args + ["--epochs", "1"]) == 0
    assert main(args + ["--epochs", "2"]) == 0  # resumes
    net = Net.load(str(out / "net.npz"))
    assert len(net.hidden) == 2


def test_selfplay_shard_and_mixed_training(tmp_path):
    from catanatron.colonist.datagen import load, run_shard as bot_shard
    from catanatron.colonist.selfplay import run_shard

    net_path = str(tmp_path / "net.npz")
    random_net(hidden=(16, 16)).save(net_path)
    sp = tmp_path / "sp"
    _, games, rows, searches, calls, _ = run_shard(
        (str(sp), net_path, 0, 3, {"simulations": 4})
    )
    assert games == 3 and rows == searches > 0
    assert calls < searches * 4  # positions from several games share calls
    bot_shard((str(tmp_path / "bot"), "W", 0, 2, 10, 0.0))
    d = load(glob.glob(str(sp / "*.npz")) + glob.glob(str(tmp_path / "bot" / "*.npz")))
    soft = d["soft"] >= 0
    assert soft.sum() == rows == len(d["pi"])
    assert np.allclose(d["pi"].astype(np.float32).sum(1), 1, atol=1e-2)
    assert not d["pi"][~d["legal"][soft]].any()
    assert (d["action"][soft] == -1).all()
    assert np.array_equal(d["v"][~soft], d["z"][~soft])

    pytest.importorskip("torch")
    from catanatron.colonist.train import main

    out = tmp_path / "model"
    assert (
        main(
            [
                str(sp),
                str(tmp_path / "bot"),
                "--out",
                str(out),
                "--hidden",
                "16",
                "--epochs",
                "1",
                "--batch",
                "64",
                "--val-fraction",
                "0.5",
                "--device",
                "cpu",
            ]
        )
        == 0
    )


def test_train_init_loads_weights(tmp_path):
    pytest.importorskip("torch")
    from catanatron.colonist.train import build_model, export, hidden_of, load_weights

    path = str(tmp_path / "start.npz")
    random_net(hidden=(16, 8), seed=3).save(path)
    assert hidden_of(path) == [16, 8]
    model = build_model([16, 8])
    load_weights(model, path)
    out = str(tmp_path / "again.npz")
    export(model, out)
    a, b = Net.load(path), Net.load(out)
    for (w1, b1), (w2, b2) in zip(
        [*a.hidden, a.policy, a.value], [*b.hidden, b.policy, b.value]
    ):
        assert np.array_equal(w1, w2) and np.array_equal(b1, b2)
    with pytest.raises(ValueError):
        load_weights(build_model([16, 16]), path)
