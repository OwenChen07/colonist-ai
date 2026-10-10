"""Phase 3: the self-play training loop."""

import csv
import json
from types import SimpleNamespace

import pytest

from catanatron.colonist import evaluate
from catanatron.colonist.diagnostics import empty_counts
from catanatron.colonist.net import random_net


def _fake_result(decision, score):
    return SimpleNamespace(
        decision=decision,
        n=2,
        score=score,
        margin=0.0,
        summary=lambda: "fake match",
        games=[SimpleNamespace(score=score, diagnostics={"RED": empty_counts()})] * 2,
    )


@pytest.fixture
def tiny_args(tmp_path):
    init = tmp_path / "init.npz"
    random_net(hidden=(16, 16)).save(str(init))
    run = tmp_path / "run"
    return (
        run,
        [
            str(run),
            "--games",
            "2",
            "--simulations",
            "2",
            "--setup-noise-scale",
            "1.8",
            "--epochs",
            "1",
            "--match-simulations",
            "2",
            "--max-games",
            "2",
            "--batch-pairs",
            "1",
            "--reference",
            "W",
            "--benchmark",
            "W",
            "--benchmark-games",
            "2",
            "--workers",
            "1",
            "--device",
            "cpu",
        ],
        str(init),
    )


def test_promotion_resume_and_opponents(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    from catanatron.colonist import loop, selfplay

    run, args, init = tiny_args
    decisions = iter([("promote", 0.75), ("keep", 0.4)])
    monkeypatch.setattr(
        evaluate, "sprt", lambda *a, **k: _fake_result(*next(decisions))
    )
    benchmarks = []
    monkeypatch.setattr(
        evaluate,
        "match",
        lambda *a, **k: benchmarks.append(a) or _fake_result(None, 0.6),
    )
    seen_opponents = []
    real_run = selfplay.run
    monkeypatch.setattr(
        selfplay,
        "run",
        lambda *a, **k: seen_opponents.append(list(k["opponents"]))
        or real_run(*a, **k),
    )

    assert loop.main(args + ["--init", init, "--rounds", "1"]) == 0
    state = json.loads((run / "state.json").read_text())
    assert state["best_round"] == 1 and state["round"] == 2
    assert (run / "best.npz").read_bytes() == (
        run / "nets" / "round_001.npz"
    ).read_bytes()
    assert len(benchmarks) == 1  # the best changed
    assert state["history"][0]["setup_noise_scale"] == 1.8
    assert (run / "round_001" / "benchmark-diagnostics.json").exists()

    # Round 2 resumes from the saved state, and plays round 0's net as an opponent.
    assert loop.main(args + ["--rounds", "2"]) == 0
    assert seen_opponents[1][0].endswith("round_000.npz") and "W" in seen_opponents[1]
    state = json.loads((run / "state.json").read_text())
    assert state["best_round"] == 1 and state["since_promotion"] == 1
    assert len(benchmarks) == 1  # not promoted: no benchmark
    rows = list(csv.DictReader(open(run / "log.csv")))
    assert [r["promoted"] for r in rows] == ["True", "False"]


def test_new_run_needs_init(tmp_path):
    from catanatron.colonist import loop

    with pytest.raises(SystemExit):
        loop.main([str(tmp_path / "run")])


def test_selfplay_opponents_are_fixed_by_seed():
    from catanatron.colonist.selfplay import seat_plan

    net = object()
    plans = [seat_plan(s, net, ["W"], 0.5) for s in range(200)]
    mixed = [p for p in plans if "W" in p.values()]
    assert 60 < len(mixed) < 140
    assert all(net in p.values() for p in plans)  # the main net always plays
    assert plans == [seat_plan(s, net, ["W"], 0.5) for s in range(200)]
    assert all("W" not in seat_plan(s, net, ["W"], 0.0).values() for s in range(50))


def test_playout_cap_gauntlet_and_live_calibration(tiny_args, monkeypatch, tmp_path):
    pytest.importorskip("torch")
    from catanatron.colonist import loop
    from catanatron.colonist.human_data import export_capture

    from .test_convert import FULL_GAME

    live = tmp_path / "live"
    export_capture(FULL_GAME, str(live), mode="value")
    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("promote", 0.7))
    played = []
    monkeypatch.setattr(
        evaluate,
        "match",
        lambda *a, **k: played.append(a[1]) or _fake_result(None, 0.6),
    )
    extra = ["--full-rate", "0.5", "--fast-simulations", "1"]
    extra += ["--gauntlet", f"{init},R", "--live-data", str(live)]
    assert loop.main(args + extra + ["--init", init, "--rounds", "1"]) == 0
    record = json.loads((run / "state.json").read_text())["history"][0]
    assert played == ["W", f"Z:{init}:2", "R"]
    assert record["gauntlet"] == {init: 0.6, "R": 0.6}
    assert record["live"]["games"] == 1 and record["live"]["positions"] > 0
    assert list((run / "round_001" / "data").glob("shard_*.npz"))


def test_full_rate_needs_fast_simulations(tiny_args):
    from catanatron.colonist import loop

    run, args, init = tiny_args
    with pytest.raises(SystemExit):
        loop.main(args + ["--init", init, "--full-rate", "0.5"])


def test_loose_gate_promotes_unless_clearly_worse(tiny_args, monkeypatch):
    """An undecided match passes the loose gate; the net is promoted after
    its benchmark, and the SPRT is 46% against 50%."""
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    seen = {}
    monkeypatch.setattr(
        evaluate,
        "sprt",
        lambda *a, **k: seen.update(k) or _fake_result(None, 0.49),
    )
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    assert loop.main(args + ["--gate", "loose", "--init", init, "--rounds", "1"]) == 0
    state = json.loads((run / "state.json").read_text())
    record = state["history"][0]
    assert (seen["p0"], seen["p1"]) == (0.46, 0.50)
    assert record["passed"] and record["promoted"] and record["gate"] == "loose"
    assert state["best_round"] == 1
    assert (run / "best.npz").read_bytes() == (
        run / "nets" / "round_001.npz"
    ).read_bytes()


def test_loose_gate_guardrail_keeps_the_best(tiny_args, monkeypatch):
    """A net that passes the match but drops on the benchmark is not promoted,
    and the best net file is untouched."""
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("promote", 0.5))
    scores = iter([0.8, 0.5])  # round 1's benchmark, then round 2's
    monkeypatch.setattr(
        evaluate, "match", lambda *a, **k: _fake_result(None, next(scores))
    )
    loose = args + ["--gate", "loose"]
    assert loop.main(loose + ["--init", init, "--rounds", "1"]) == 0
    best = (run / "best.npz").read_bytes()
    assert loop.main(loose + ["--rounds", "2"]) == 0
    state = json.loads((run / "state.json").read_text())
    record = state["history"][1]
    assert record["passed"] and not record["promoted"]
    assert "benchmark 0.5000 vs best's 0.8000" in record["guardrails"][0]
    assert state["best_round"] == 1 and state["since_promotion"] == 1
    assert (run / "best.npz").read_bytes() == best


def test_strict_gate_keeps_an_undecided_net(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result(None, 0.51))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    assert loop.main(args + ["--init", init, "--rounds", "1"]) == 0
    state = json.loads((run / "state.json").read_text())
    assert not state["history"][0]["promoted"] and state["best_round"] == 0


def test_train_sample_reaches_the_trainer(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    from catanatron.colonist import loop
    from catanatron.colonist import train as T

    run, args, init = tiny_args
    seen = []
    real = T.main
    monkeypatch.setattr(T, "main", lambda argv: seen.append(argv) or real(argv))
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("keep", 0.4))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    extra = ["--train-sample", "50", "--init", init, "--rounds", "1"]
    assert loop.main(args + extra) == 0
    assert seen[0][seen[0].index("--sample") + 1] == "50"


def test_training_uses_the_threads_it_is_given(tiny_args, monkeypatch):
    torch = pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    seen = []
    monkeypatch.setattr(torch, "set_num_threads", seen.append)
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("keep", 0.4))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    assert (
        loop.main(args + ["--train-threads", "3", "--init", init, "--rounds", "1"]) == 0
    )
    assert seen == [3]
