"""Promoted checkpoints are installed safely for cloud ranked play."""

import json
import os

import numpy as np
import pytest

from catanatron.colonist.ranked_runner import (
    install_promoted_model,
    promoted_checkpoint,
)


def test_promoted_checkpoint_is_atomic_and_only_changes_for_new_content(tmp_path):
    run = tmp_path / "training"
    nets = run / "nets"
    nets.mkdir(parents=True)
    np.savez(nets / "round_005.npz", weights=np.array([5]))
    (run / "state.json").write_text(json.dumps({"best_round": 5}))
    round_number, source = promoted_checkpoint(run)
    assert round_number == 5
    destination = tmp_path / "ranked" / "model.npz"
    assert install_promoted_model(source, destination)
    assert not install_promoted_model(source, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [5]

    np.savez(nets / "round_006.npz", weights=np.array([6]))
    (run / "state.json").write_text(json.dumps({"best_round": 6}))
    round_number, source = promoted_checkpoint(run)
    assert round_number == 6
    assert install_promoted_model(source, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [6]


def test_invalid_promoted_checkpoint_keeps_current_model(tmp_path):
    good = tmp_path / "good.npz"
    bad = tmp_path / "bad.npz"
    destination = tmp_path / "model.npz"
    np.savez(good, weights=np.array([5]))
    bad.write_bytes(b"incomplete checkpoint")
    install_promoted_model(good, destination)
    with pytest.raises(ValueError):
        install_promoted_model(bad, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [5]


def test_each_verified_game_is_listed_with_its_net(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import record_game, round_label

    net = tmp_path / "model.npz"
    net.write_bytes(b"weights")
    conversion = SimpleNamespace(
        ended="win",
        record={
            "winner": "RED",
            "players": {"RED": {"you": True}, "BLUE": {"you": False}},
        },
    )
    label = round_label(tmp_path / "runs" / "sp1", 8)
    assert label == "sp1 round 8"
    record_game(tmp_path, "captures/bridge-1.json", conversion, net, label, 8, 800)
    (row,) = [json.loads(line) for line in open(tmp_path / "games.jsonl")]
    assert row["capture"] == "bridge-1.json" and row["round"] == 8
    assert row["bot_color"] == "RED" and row["winner"] == "RED"
    assert row["label"] == label and len(row["net_sha256"]) == 16


def test_a_resignation_names_the_other_player_as_winner(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import record_game

    net = tmp_path / "model.npz"
    net.write_bytes(b"weights")
    record = {
        "seats": ["RED", "BLUE"],
        "resigned": "RED",
        "forfeit": True,
        "players": {"RED": {"you": False}, "BLUE": {"you": True}},
    }
    conversion = SimpleNamespace(ended="resignation", record=record)
    row = record_game(tmp_path, "bridge-2.json", conversion, net, "x", 8, 800)
    assert row["winner"] == "BLUE" == row["bot_color"]


def test_the_bridge_runs_with_one_math_thread(monkeypatch):
    from catanatron.colonist.ranked_runner import bridge_env

    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")
    env = bridge_env()
    assert env["OPENBLAS_NUM_THREADS"] == env["OMP_NUM_THREADS"] == "1"
    assert env["PATH"] == os.environ["PATH"]


def test_frame_cap_script_spaces_out_animation_frames():
    import shutil
    import subprocess

    from catanatron.colonist.ranked_runner import frame_cap_script

    script = frame_cap_script(5)
    assert "200.0" in script and "requestAnimationFrame" in script
    if shutil.which("node") is None:
        return
    # A fake 60 fps display running a game's render loop for 10 seconds
    harness = """
    let t = 0, queue = [];
    globalThis.window = { requestAnimationFrame: (cb) => queue.push(cb) };
    eval(SCRIPT);
    let drawn = 0;
    const draw = () => { drawn++; window.requestAnimationFrame(draw); };
    window.requestAnimationFrame(draw);
    for (let f = 0; f < 600; f++) {
      t += 1000 / 60; const run = queue; queue = []; run.forEach((cb) => cb(t));
    }
    console.log(drawn);
    """.replace(
        "SCRIPT", json.dumps(script)
    )
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert 45 <= int(out.stdout) <= 51  # about 5 a second, not 60
