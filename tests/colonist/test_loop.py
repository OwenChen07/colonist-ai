"""Phase 3: the self-play training loop."""

import csv
import json
import os
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
            "--min-games",
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


def test_confidence_gate_uses_fixed_pairs_and_unrounded_lower_bound(
    tmp_path, monkeypatch
):
    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    run.state = {"best_round": 0, "since_promotion": 0, "history": []}
    calls = []

    def fixed(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(
            n=2000, score=0.521, margin=0.021, summary=lambda: "fixed match"
        )

    monkeypatch.setattr(evaluate, "match", fixed)
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **kw: pytest.fail("SPRT used"))
    args = SimpleNamespace(
        gate="confidence",
        match_simulations=100,
        max_games=2000,
        min_games=1000,
        min_score=0,
        seed=1,
        workers=1,
        distribute_eval=False,
    )
    loop.match(run, 1, args)
    record = run.state["history"][0]
    assert calls[0]["games"] == 2000
    assert record["passed"] and record["lower_bound"] == 0.5
    assert record["decision"] == "promote"
    calls.clear()
    monkeypatch.setattr(
        evaluate,
        "match",
        lambda *a, **kw: SimpleNamespace(
            n=2000,
            score=0.52049,
            margin=0.02051,
            summary=lambda: "rounded to 52.0 ± 2.1",
        ),
    )
    loop.match(run, 2, args)
    assert not run.state["history"][-1].get("passed")


def test_confidence_gate_blocks_only_a_clear_human_regression(tmp_path, monkeypatch):
    from catanatron.colonist import calibrate, failure_set, loop, net

    run = loop.Run(str(tmp_path / "run"))
    run.state = {"best_round": 0, "history": [], "human_confirmation_after_ns": 0}
    monkeypatch.setattr(calibrate, "load_value_shards", lambda _: [object()])
    monkeypatch.setattr(
        calibrate, "calibrate", lambda *_: {"mse": 0.6052, "accuracy": 0.7679}
    )
    better = {
        "games": 200,
        "mse": {"improvement": 0.002, "lower": 0.001},
        "accuracy": {"improvement": 0.003, "lower": -0.001},
    }
    monkeypatch.setattr(calibrate, "paired_game_comparison", lambda *_: better)
    monkeypatch.setattr(net.Net, "load", lambda *_: object())
    monkeypatch.setattr(
        loop, "fresh_human_paths", lambda *_: [str(tmp_path / "fresh")] * 30
    )
    monkeypatch.setattr(
        loop, "load_value_shards_from_paths", lambda *_: [object()] * 30
    )
    monkeypatch.setattr(loop.os.path, "isfile", lambda *_: True)
    monkeypatch.setattr(loop.os, "stat", lambda *_: SimpleNamespace(st_mtime_ns=1))
    monkeypatch.setattr(failure_set, "compare", lambda *_a, **_k: {"positions": 50})
    args = SimpleNamespace(
        gate="confidence",
        anchor="",
        benchmark="",
        live_data=["heldout"],
        guard_live=0.01,
        min_human_games=100,
        min_fresh_human_games=30,
        human_mse_margin=0.01,
        human_accuracy_margin=0.01,
        min_tactical_positions=30,
        tactical_margin=2,
        tactical_index="index",
        tactical_captures="captures",
        match_simulations=100,
    )
    record = {"live": {"mse": 0.6037, "accuracy": 0.7682}}
    assert loop.guardrails(run, 1, args, record) == (True, [])
    assert record["best_live_mse"] == 0.6052
    assert record["best_live_accuracy"] == 0.7679

    # No better, but not clearly worse (round 57's case): it passes.
    monkeypatch.setattr(
        calibrate,
        "paired_game_comparison",
        lambda *_: {**better, "mse": {"improvement": -0.002, "lower": -0.007}},
    )
    record = {"live": {"mse": 0.6052, "accuracy": 0.7679}}
    assert loop.guardrails(run, 1, args, record) == (True, [])
    # Its interval reaches a clear regression: held back.
    monkeypatch.setattr(
        calibrate,
        "paired_game_comparison",
        lambda *_: {**better, "accuracy": {"improvement": -0.01, "lower": -0.02}},
    )
    ok, reasons = loop.guardrails(run, 1, args, record)
    assert not ok and any("possible regression" in r for r in reasons)
    args.live_data = []
    assert not loop.guardrails(run, 1, args, record)[0]


def test_confidence_gate_skips_too_few_fresh_games_and_blocks_tactical_regression(
    tmp_path, monkeypatch
):
    from catanatron.colonist import calibrate, failure_set, loop, net

    run = loop.Run(str(tmp_path / "run"))
    run.state = {"best_round": 0, "history": [], "human_confirmation_after_ns": 5}
    good = {
        "games": 200,
        "mse": {"improvement": 0.003, "lower": 0.001},
        "accuracy": {"improvement": 0.002, "lower": -0.002},
    }
    monkeypatch.setattr(calibrate, "load_value_shards", lambda _: [object()])
    monkeypatch.setattr(
        calibrate, "calibrate", lambda *_: {"mse": 0.6, "accuracy": 0.7}
    )
    monkeypatch.setattr(calibrate, "paired_game_comparison", lambda *_: good)
    monkeypatch.setattr(net.Net, "load", lambda *_: object())
    monkeypatch.setattr(loop.os.path, "isfile", lambda *_: True)
    monkeypatch.setattr(
        failure_set,
        "compare",
        lambda *_a, **_k: tactical,
    )
    # Round 56's case: one more miss than the best is within the margin.
    tactical = {
        "positions": 300,
        "baseline_immediate_win_missed": 4,
        "candidate_immediate_win_missed": 5,
    }
    monkeypatch.setattr(loop, "fresh_human_paths", lambda *_: [])
    args = SimpleNamespace(
        gate="confidence",
        anchor="",
        benchmark="",
        live_data=["heldout"],
        guard_live=0.01,
        min_human_games=100,
        min_fresh_human_games=30,
        human_mse_margin=0.01,
        human_accuracy_margin=0.01,
        min_tactical_positions=200,
        tactical_margin=2,
        tactical_index="index",
        tactical_captures="captures",
        match_simulations=100,
    )
    assert loop.guardrails(run, 1, args, {"live": {"mse": 0.59}}) == (True, [])
    tactical["candidate_immediate_win_missed"] = 7
    ok, reasons = loop.guardrails(run, 1, args, {"live": {"mse": 0.59}})
    assert not ok
    # Too few fresh games is no longer a reason to wait.
    assert not any("fresh" in reason for reason in reasons)
    assert any("tactical regression" in reason for reason in reasons)
    assert run.state["human_confirmation_after_ns"] == 5


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


def test_a_net_below_the_minimum_score_is_kept(tiny_args, monkeypatch):
    """An undecided match passes the loose gate, but 47.95% is below a 50%
    minimum against the best, so the best net stays."""
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result(None, 0.4795))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.9))
    loose = args + ["--gate", "loose", "--min-score", "0.5", "--init", init]
    assert loop.main(loose + ["--rounds", "1"]) == 0
    state = json.loads((run / "state.json").read_text())
    assert not state["history"][0]["promoted"] and state["best_round"] == 0


def test_a_shard_subset_stays_near_the_position_limit(tmp_path):
    import numpy as np

    from catanatron.colonist.train import select_shards

    paths = []
    for i in range(10):
        path = tmp_path / f"shard_{i}.npz"
        np.savez(path, game=np.zeros(100, np.int32))
        paths.append(str(path))
    chosen = select_shards(paths, 350, seed=1)
    assert len(chosen) == 4 and set(chosen) <= set(paths)  # 400 >= 350
    assert select_shards(paths, 350, seed=1) == chosen  # the seed decides
    assert len(select_shards(paths + paths[:1], 10_000)) == 11  # a repeat counts


def test_training_on_a_gpu_host_and_falling_back_here(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    import shutil

    from catanatron.colonist import loop
    from catanatron.colonist import train as T

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("keep", 0.4))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    local = []
    real = T.main
    monkeypatch.setattr(T, "main", lambda argv: local.append(argv) or real(argv))

    # The GPU host trains: its net comes back and nothing trains here.
    def remote(a, argv, data, out, init_net, n):
        os.makedirs(out, exist_ok=True)
        shutil.copyfile(init_net, os.path.join(out, "net.npz"))

    monkeypatch.setattr(loop, "train_remote", remote)
    extra = ["--train-host", "me@gpu", "--init", init, "--rounds", "1"]
    assert loop.main(args + extra) == 0
    assert not local and (run / "nets" / "round_001.npz").exists()

    # It's unreachable: this machine trains instead.
    def down(*a):
        raise OSError("no route to host")

    monkeypatch.setattr(loop, "train_remote", down)
    assert loop.main(args + ["--train-host", "me@gpu", "--rounds", "2"]) == 0
    assert local and (run / "nets" / "round_002.npz").exists()


def test_the_gpu_host_gets_its_paths_and_settings(tmp_path, monkeypatch):
    from catanatron.colonist import distributed as D
    from catanatron.colonist import loop

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLONIST_WORKER_ROOT", "/home/me/repo")
    commands, copies = [], []
    monkeypatch.setattr(D, "_sync_code", lambda *a: None)
    monkeypatch.setattr(D, "_rsync", lambda key, src, dst: copies.append((src, dst)))

    def run(cmd, **kw):
        commands.append(cmd)
        if "gpu.lock" in cmd[-1]:
            return SimpleNamespace(returncode=1)  # not busy
        if "catanatron.colonist.train" in cmd[-1]:
            os.makedirs("runs/g/round_002/train", exist_ok=True)
            open("runs/g/round_002/train/net.npz", "w").close()

    import subprocess

    monkeypatch.setattr(subprocess, "run", run)
    args = SimpleNamespace(
        train_host="me@gpu", train_device="cuda", train_max_positions=4_000_000
    )
    data = ["runs/g/round_001/data", "data/human", "data/human"]
    argv = [*data, "--out", "runs/g/round_002/train", "--init", "runs/g/best.npz"]
    loop.train_remote(args, argv, data, "runs/g/round_002/train", "runs/g/best.npz", 2)
    train = commands[-1][-1]
    assert "/home/me/repo/data/human /home/me/repo/data/human" in train
    assert "--out /home/me/repo/runs/g/round_002/train" in train
    assert "--device cuda --data-device cpu --max-positions 4000000" in train
    assert ("data/human/", "me@gpu:/home/me/repo/data/human/") in copies
    assert copies.count(("data/human/", "me@gpu:/home/me/repo/data/human/")) == 1
    assert copies[-1] == (
        "me@gpu:/home/me/repo/runs/g/round_002/train/",
        "runs/g/round_002/train/",
    )


def test_a_busy_gpu_host_is_skipped(tmp_path, monkeypatch):
    import subprocess

    from catanatron.colonist import distributed as D
    from catanatron.colonist import loop

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(D, "_sync_code", lambda *a: pytest.fail("synced"))
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0)
    )
    sleeps = []
    monkeypatch.setattr(loop.time, "sleep", sleeps.append)
    args = SimpleNamespace(
        train_host="me@gpu", train_device="cuda", train_max_positions=1
    )
    # It waits GPU_LOCK_WAIT, checking every GPU_LOCK_POLL, then gives up.
    with pytest.raises(RuntimeError, match="busy"):
        loop.train_remote(args, ["d", "--out", "o"], ["d"], "o", "b.npz", 1)
    assert sleeps == [loop.GPU_LOCK_POLL] * (loop.GPU_LOCK_WAIT // loop.GPU_LOCK_POLL)


def test_training_waits_for_the_gpu_lock_to_go(tmp_path, monkeypatch):
    import subprocess

    from catanatron.colonist import distributed as D
    from catanatron.colonist import loop

    monkeypatch.chdir(tmp_path)
    synced, sleeps, checks = [], [], []
    monkeypatch.setattr(D, "_sync_code", lambda *a: synced.append(1))
    monkeypatch.setattr(D, "_rsync", lambda *a: None)
    monkeypatch.setattr(loop.time, "sleep", sleeps.append)

    def run(cmd, **kw):
        if "gpu.lock" in cmd[-1]:
            checks.append(1)
            return SimpleNamespace(returncode=0 if len(checks) <= 3 else 1)
        if "catanatron.colonist.train" in cmd[-1]:
            os.makedirs("o", exist_ok=True)
            open("o/net.npz", "w").close()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    args = SimpleNamespace(
        train_host="me@gpu", train_device="cuda", train_max_positions=1
    )
    loop.train_remote(args, ["d", "--out", "o"], ["d"], "o", "n/b.npz", 1)
    assert sleeps == [loop.GPU_LOCK_POLL] * 3 and synced


def test_strict_gate_promotes_only_through_the_guardrails(tiny_args, monkeypatch):
    """A clearly better net (strict gate) still has to hold its guardrails:
    here it beats last round's net but slides against the fixed anchor."""
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("promote", 0.6))
    played = []

    def fake_match(new, opponent, **k):
        played.append((new, opponent, k["games"]))
        if "anchor" not in opponent:
            return _fake_result(None, 0.9)  # benchmark and gauntlet
        return _fake_result(None, 0.6 if "round_000" in new else 0.55)

    monkeypatch.setattr(evaluate, "match", fake_match)
    anchor = str(run.parent / "anchor.npz")
    with open(init, "rb") as source, open(anchor, "wb") as copy:
        copy.write(source.read())
    strict = args + ["--anchor", anchor, "--anchor-games", "4"]
    assert loop.main(strict + ["--init", init, "--rounds", "1"]) == 0
    state = json.loads((run / "state.json").read_text())
    record = state["history"][0]
    assert record["passed"] and not record["promoted"]
    assert record["anchor"] == 0.55 and record["best_anchor"] == 0.6
    assert "anchor 0.55 vs best's 0.6" in record["guardrails"][0]
    assert state["best_round"] == 0 and state["anchor"]["round"] == 0
    anchor_games = [p for p in played if "anchor" in p[1]]
    assert [g for _, _, g in anchor_games] == [4, 4]  # the best once, then round 1


def test_the_before_round_command_runs_and_a_failure_is_not_fatal(
    tiny_args, monkeypatch
):
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result(None, 0.5))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    marker = run.parent / "refreshed"
    hook = ["--before-round", f"touch {marker}"]
    assert loop.main(args + hook + ["--init", init, "--rounds", "1"]) == 0
    assert marker.exists()
    failing = ["--before-round", "exit 3"]
    assert loop.main(args + failing + ["--rounds", "2"]) == 0  # played on
    assert json.loads((run / "state.json").read_text())["round"] == 3


def test_a_round_played_early_skips_its_hook_and_self_play(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    import shutil

    from catanatron.colonist import loop, selfplay

    run, args, init = tiny_args
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result(None, 0.5))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    assert loop.main(args + ["--init", init, "--rounds", "1"]) == 0
    # Another run whose round 1 shards are all on disk, as after early self-play.
    other = run.parent / "other"
    shutil.copytree(run / "round_001" / "data", other / "round_001" / "data")

    def no_selfplay(*a, **k):
        raise AssertionError("self-play ran for a complete round")

    monkeypatch.setattr(selfplay, "run", no_selfplay)
    marker = run.parent / "refreshed"
    hook = ["--before-round", f"touch {marker}"]
    argv = [str(other)] + args[1:] + hook + ["--init", init, "--rounds", "1"]
    assert loop.main(argv) == 0
    assert not marker.exists()
    assert json.loads((other / "state.json").read_text())["round"] == 2


def _match_result(score, games, seed=0):
    """A real MatchResult whose games score `score` on average."""
    wins = round(score * games)
    return evaluate.MatchResult(
        "a",
        "b",
        [
            evaluate.GameResult(
                seed + i // 2,
                i % 2 == 0,
                1.0 if i < wins else 0.0,
                50,
                diagnostics={"RED": empty_counts()},
            )
            for i in range(games)
        ],
    )


def test_progress_gate_extends_a_near_miss_and_judges_all_games(tiny_args, monkeypatch):
    """51.5% after the first games is a near miss: more games are played on
    other seeds, and the total (here 51.25%) passes at 51%."""
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    calls = []

    def fake_match(new, opponent, games, seed, **k):
        calls.append((games, seed))
        if "round_" in opponent and len(calls) == 1:
            return _match_result(0.515, 200, seed)
        if "round_" in opponent and len(calls) == 2:
            return _match_result(0.51, 200, seed)
        return _match_result(0.6, 4, seed)  # benchmark

    monkeypatch.setattr(evaluate, "match", fake_match)
    gate = ["--gate", "progress", "--max-games", "200", "--extend-games", "200"]
    assert loop.main(args + gate + ["--init", init, "--rounds", "1"]) == 0
    record = json.loads((run / "state.json").read_text())["history"][0]
    assert calls[0][0] == 200 and calls[1][0] == 200 and calls[1][1] != calls[0][1]
    assert record["games"] == 400 and record["score"] == 0.5125
    assert record["passed"] and record["decision"] == "promote"


def test_progress_gate_keeps_a_clear_miss_without_extra_games(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    calls = []
    monkeypatch.setattr(
        evaluate,
        "match",
        lambda new, opponent, games, seed, **k: calls.append(games)
        or _match_result(0.49, 200, seed),
    )
    gate = ["--gate", "progress", "--max-games", "200", "--extend-games", "200"]
    assert loop.main(args + gate + ["--init", init, "--rounds", "1"]) == 0
    record = json.loads((run / "state.json").read_text())["history"][0]
    assert calls.count(200) == 1 and not record.get("passed")  # no extension


def test_training_continues_from_the_previous_round_unless_it_was_clearly_worse(
    tmp_path, monkeypatch
):
    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    init = tmp_path / "init.npz"
    random_net(hidden=(8,)).save(str(init))
    run.start(str(init))
    args = SimpleNamespace(train_from="latest", reset_below=0.48)
    assert loop.train_init(run, 1, args)[0] == run.best  # no previous round
    os.makedirs(os.path.dirname(run.net(3)), exist_ok=True)
    random_net(hidden=(8,)).save(run.net(3))
    run.state["history"].append({"round": 3, "score": 0.512})
    assert loop.train_init(run, 4, args)[0] == run.net(3)
    run.state["history"][-1]["score"] = 0.45  # clearly worse: back to the best
    assert loop.train_init(run, 4, args)[0] == run.best
    best_args = SimpleNamespace(train_from="best", reset_below=0.48)
    run.state["history"][-1]["score"] = 0.55
    assert loop.train_init(run, 4, best_args)[0] == run.best


def _confidence_match(tiny_args, monkeypatch, first, second=0.5):
    """Round 1's confidence-gate match with the given scores; returns the
    match calls (games) and the round's record."""
    from catanatron.colonist import loop

    run, args, init = tiny_args
    calls = []

    def fake_match(new, opponent, games, seed, **k):
        if "round_" not in opponent:
            return _match_result(0.6, 4, seed)  # benchmark
        calls.append(games)
        return _match_result(first if len(calls) == 1 else second, games, seed)

    monkeypatch.setattr(evaluate, "match", fake_match)
    gate = ["--gate", "confidence", "--max-games", "2000", "--extend-games", "2000"]
    assert loop.main(args + gate + ["--init", init, "--rounds", "1"]) == 0
    return calls, json.loads((run / "state.json").read_text())["history"][0]


def test_confidence_gate_extends_a_near_miss_and_judges_all_games(
    tiny_args, monkeypatch
):
    pytest.importorskip("torch")
    calls, record = _confidence_match(tiny_args, monkeypatch, 0.51, 0.52)
    assert calls == [2000, 2000]
    assert record["games"] == 4000 and record["score"] == 0.515
    assert record["lower_bound"] == pytest.approx(0.515 - record["margin"])


def test_confidence_gate_plays_no_extra_games_for_a_clear_miss(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    calls, record = _confidence_match(tiny_args, monkeypatch, 0.49)
    assert calls == [2000] and record["games"] == 2000
    assert record["decision"] == "keep"


def test_a_clearly_losing_match_stops_early_on_the_same_boards(tmp_path, monkeypatch):
    """With --futility-games, a match whose 95% interval can't reach the bar
    stops; otherwise the rest is played on the next seeds, so the boards are
    exactly the long match's."""
    from catanatron.colonist import loop

    calls = []

    def fake_match(new, best, games, seed, **k):
        calls.append((games, seed))
        return _match_result(score, games, seed)

    monkeypatch.setattr(evaluate, "match", fake_match)
    args = SimpleNamespace(max_games=2000, futility_games=1000, futility_below=0.505)
    score = 0.44  # clearly losing: stops
    result = loop._fixed_match("a", "b", args, {"seed": 100})
    assert calls == [(1000, 100)] and result.n == 1000
    calls.clear()
    score = 0.5  # could still pass: the rest, on the following boards
    result = loop._fixed_match("a", "b", args, {"seed": 100})
    assert calls == [(1000, 100), (1000, 600)] and result.n == 2000
    calls.clear()
    args.futility_games = 0  # off: one match
    loop._fixed_match("a", "b", args, {"seed": 100})
    assert calls == [(2000, 100)]


def test_prefetch_starts_the_next_round_once_and_records_when_done(
    tmp_path, monkeypatch
):
    import subprocess

    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    init = tmp_path / "init.npz"
    random_net(hidden=(8,)).save(str(init))
    run.start(str(init))
    started = []

    class FakeProcess:
        pid = os.getpid()  # "alive"

    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **k: started.append(argv) or FakeProcess()
    )
    args = SimpleNamespace(
        prefetch=True, prefetch_round=0, argv=[run.root, "--x"], train_host="gpu@pc"
    )
    envs = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda argv, env=None, **k: envs.append(env)
        or started.append(argv)
        or FakeProcess(),
    )
    loop.ensure_prefetch(run, 5, args)
    assert envs[0]["COLONIST_EXCLUDE_HOSTS"] == "gpu@pc"  # not on the trainer
    loop.ensure_prefetch(run, 5, args)  # running: not again
    assert len(started) == 1 and started[0][-2:] == ["--prefetch-round", "5"]
    assert started[0][3:5] == [run.root, "--x"]
    # The early self-play itself: plays round 5 and records it's done.
    played = []
    monkeypatch.setattr(loop, "play", lambda run, n, args: played.append(n))
    loop.prefetch_only(run, 5, args)
    assert played == [5]
    info = json.load(open(os.path.join(run.root, "prefetch.json")))
    assert info["round"] == 5 and info["done"]
    FakeProcess.pid = 2**22 + 12345  # gone: but round 5 is done, so not again
    loop.ensure_prefetch(run, 5, args)
    assert len(started) == 1
    loop.wait_prefetch(run, 5)  # nothing running: returns at once
    off = SimpleNamespace(prefetch=False, prefetch_round=0, argv=[])
    loop.ensure_prefetch(run, 6, off)
    assert len(started) == 1


def test_the_loop_starts_prefetch_after_self_play_and_waits_before_it(
    tiny_args, monkeypatch
):
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    calls = []
    monkeypatch.setattr(
        loop, "ensure_prefetch", lambda run, n, args: calls.append(("start", n))
    )
    monkeypatch.setattr(loop, "wait_prefetch", lambda run, n: calls.append(("wait", n)))
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result("keep", 0.4))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    assert loop.main(args + ["--prefetch", "--init", init, "--rounds", "1"]) == 0
    assert calls[0] == ("wait", 1) and ("start", 2) in calls


def test_selfplay_uses_the_newest_good_net_or_the_best(tmp_path):
    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    init = tmp_path / "init.npz"
    random_net(hidden=(8,)).save(str(init))
    run.start(str(init))
    run.state["best_round"] = 40
    for k in (57, 58):
        random_net(hidden=(8,)).save(run.net(k))
    run.state["history"] += [
        {"round": 57, "score": 0.522},
        {"round": 58, "score": 0.51},
    ]
    latest = SimpleNamespace(selfplay_from="latest", reset_below=0.48)
    assert loop.selfplay_net(run, 59, latest)[0] == run.net(58)
    # Early self-play for round 60 starts while round 59 trains: round 58's.
    assert loop.selfplay_net(run, 60, latest)[0] == run.net(58)
    run.state["history"].append({"round": 59, "score": 0.45})  # clearly worse
    random_net(hidden=(8,)).save(run.net(59))
    assert loop.selfplay_net(run, 60, latest)[0] == run.best
    best = SimpleNamespace(selfplay_from="best", reset_below=0.48)
    assert loop.selfplay_net(run, 59, best)[0] == run.best
    run.state["best_round"] = 59  # promoted: nothing newer yet
    assert loop.selfplay_net(run, 60, latest)[0] == run.best


def test_the_anchor_is_scored_again_at_a_new_search_budget(tmp_path, monkeypatch):
    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    run.state = {"best_round": 40, "history": []}
    run.save = lambda: None
    played = []
    monkeypatch.setattr(
        loop, "_anchor_match", lambda run, net, args: played.append(net) or 0.6
    )
    run.state["anchor"] = {"round": 40, "net": "a.npz", "score": 0.5865}  # at 100
    args = SimpleNamespace(anchor="a.npz", match_simulations=100)
    assert loop.anchor_reference(run, args) == 0.5865 and not played
    args.match_simulations = 200
    assert loop.anchor_reference(run, args) == 0.6 and len(played) == 1
    assert run.state["anchor"]["simulations"] == 200


def test_a_slow_early_self_play_is_stopped_after_the_wait_limit(tmp_path, monkeypatch):
    import signal
    import subprocess

    from catanatron.colonist import loop

    run = loop.Run(str(tmp_path / "run"))
    os.makedirs(run.root)
    child = subprocess.Popen(["sleep", "60"])
    with open(os.path.join(run.root, "prefetch.json"), "w") as stream:
        json.dump({"round": 7, "pid": child.pid}, stream)
    killed = []
    real_kill = os.kill
    monkeypatch.setattr(
        loop.os,
        "kill",
        lambda pid, sig: (sig == signal.SIGTERM and killed.append(pid))
        or real_kill(pid, sig),
    )
    loop.wait_prefetch(run, 7, limit=0)
    assert killed == [child.pid]
    assert child.wait(timeout=5) is not None


def test_a_promotion_is_saved_before_the_gauntlet(tiny_args, monkeypatch):
    pytest.importorskip("torch")
    from catanatron.colonist import loop

    run, args, init = tiny_args
    seen = []

    def gauntlet(run_, n, args_):
        saved = json.loads((run / "state.json").read_text())
        seen.append(saved["best_round"])

    monkeypatch.setattr(loop, "gauntlet", gauntlet)
    monkeypatch.setattr(evaluate, "sprt", lambda *a, **k: _fake_result(None, 0.49))
    monkeypatch.setattr(evaluate, "match", lambda *a, **k: _fake_result(None, 0.6))
    gate = ["--gate", "loose", "--gauntlet", "W", "--init", init, "--rounds", "1"]
    assert loop.main(args + gate) == 0
    assert seen == [1]  # on disk already when the gauntlet started
