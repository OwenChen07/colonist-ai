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


def test_a_pinned_round_is_played_instead_of_the_promoted_one(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import main, pin_round_arg, record_game

    run = tmp_path / "training"
    nets = run / "nets"
    nets.mkdir(parents=True)
    np.savez(nets / "round_066.npz", weights=np.array([66]))
    np.savez(nets / "round_069.npz", weights=np.array([69]))
    (run / "state.json").write_text(json.dumps({"best_round": 66}))
    assert promoted_checkpoint(run, None) == (66, (nets / "round_066.npz").resolve())
    assert promoted_checkpoint(run, 69) == (69, (nets / "round_069.npz").resolve())
    # A promotion doesn't move the pin.
    (run / "state.json").write_text(json.dumps({"best_round": 70}))
    assert promoted_checkpoint(run, 69)[0] == 69
    with pytest.raises(RuntimeError):
        promoted_checkpoint(run, 71)

    # The service passes --pin-round=${PIN_ROUND}, empty when unset.
    assert pin_round_arg("") is None and pin_round_arg("69") == 69
    with pytest.raises(SystemExit):
        main(["--net", str(tmp_path / "model.npz"), "--pin-round=69"])

    net = tmp_path / "model.npz"
    net.write_bytes(b"weights")
    conversion = SimpleNamespace(
        ended="win",
        record={"winner": "RED", "players": {"RED": {"you": True}}},
    )
    row = record_game(tmp_path, "c.json", conversion, net, "g1 round 69", 69, 800)
    assert "pinned" not in row
    row = record_game(
        tmp_path, "d.json", conversion, net, "g1 round 69", 69, 800, pinned=True
    )
    assert row["pinned"] is True and row["round"] == 69


def test_a_fixed_net_is_labelled_by_name_and_date_and_reloads_on_change(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import (
        file_stamp,
        fixed_net_label,
        main,
        record_game,
    )

    people = tmp_path / "human" / "net.npz"
    people.parent.mkdir()
    np.savez(people, weights=np.array([1]))
    # Oct 19, 2026, 4:50 PM in Toronto (20:50 UTC, daylight time).
    os.utime(people, (1792443000, 1792443000))
    assert fixed_net_label(people, "people model") == "people model, Oct 19, 4:50 PM"
    assert fixed_net_label(people) == "net.npz, Oct 19, 4:50 PM"
    stamp = file_stamp(people)
    destination = tmp_path / "bot8" / "model.npz"
    assert install_promoted_model(people, destination)
    # The people model's refresh replaces the file: a new stamp, a new install.
    np.savez(people, weights=np.array([2]))
    os.utime(people, (1792446600, 1792446600))
    assert file_stamp(people) != stamp
    assert install_promoted_model(people, destination)
    with np.load(destination) as net:
        assert net["weights"].tolist() == [2]
    assert file_stamp(tmp_path / "missing.npz") is None

    with pytest.raises(SystemExit):
        main(["--net", str(destination), "--net-path", str(tmp_path / "none.npz")])

    conversion = SimpleNamespace(
        ended="win", record={"winner": "RED", "players": {"RED": {"you": True}}}
    )
    label = fixed_net_label(people, "people model")
    row = record_game(
        tmp_path, "c.json", conversion, destination, label, None, 2400, fixed_net=True
    )
    assert row["fixed_net"] is True and row["round"] is None
    assert row["label"] == "people model, Oct 19, 5:50 PM"


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


def test_extension_copy_points_at_its_bridge_port(tmp_path):
    from pathlib import Path

    from catanatron.colonist.ranked_runner import extension_for_port

    source = Path(__file__).resolve().parents[2] / "bot"
    assert extension_for_port(source, 8765, tmp_path) == source
    copy = extension_for_port(source, 8766, tmp_path)
    worker = (copy / "src" / "background.js").read_text()
    assert "ws://127.0.0.1:8766" in worker and "8765" not in worker
    assert (copy / "manifest.json").exists()
    assert "8765" in (source / "src" / "background.js").read_text()  # untouched


def test_games_against_our_own_bots_are_marked(tmp_path):
    from types import SimpleNamespace

    from catanatron.colonist.ranked_runner import opponent_name, record_game

    net = tmp_path / "model.npz"
    net.write_bytes(b"weights")
    record = {
        "seats": ["RED", "BLUE"],
        "winner": "RED",
        "players": {
            "RED": {"you": True, "name": "CatBot77"},
            "BLUE": {"you": False, "name": "OurBot2"},
        },
    }
    assert opponent_name(record) == "OurBot2"
    conversion = SimpleNamespace(ended="win", record=record)
    row = record_game(
        tmp_path,
        "b.json",
        conversion,
        net,
        "g1 round 5",
        5,
        400,
        account="casual1",
        queue="casual",
        own_bot=True,
    )
    assert row["own_bot"] and row["queue"] == "casual" and row["account"] == "casual1"
    plain = record_game(tmp_path, "c.json", conversion, net, "x", 5, 800)
    assert "own_bot" not in plain and plain["queue"] == "ranked"


def test_a_capture_must_come_from_the_expected_queue(monkeypatch):
    from types import SimpleNamespace

    from catanatron.colonist import ranked_runner as R

    casual = SimpleNamespace(
        record={"source": {"ranked": False}}, unsupported=[], ended="win"
    )
    monkeypatch.setattr(R, "load_capture", lambda path: None)
    monkeypatch.setattr(R, "convert", lambda frames: casual)
    assert R._finished_ranked_capture("x", ranked=False) is casual
    with pytest.raises(ValueError, match="not a ranked game"):
        R._finished_ranked_capture("x", ranked=True)


def test_a_game_without_opponent_decisions_skips_that_export(
    monkeypatch, tmp_path, capsys
):
    """An opponent who leaves before placing anything gives no opening
    positions (seen live); the runner must go on to the next game."""
    from pathlib import Path

    from catanatron.colonist import ranked_runner as R

    def export(path, out_dir, mode):
        if mode == "opponent_openings":
            raise ValueError("capture has no non-forced decisions")
        return os.path.join(out_dir, "shard.npz"), 2, 2

    monkeypatch.setattr(R, "export_capture", export)
    R.export_training_data(Path("game.json"), tmp_path)
    out = capsys.readouterr().out
    assert "value: 2 positions" in out
    assert "opponent_openings: skipped (capture has no non-forced decisions)" in out


def test_a_new_account_plays_casual_until_ranked_unlocks(tmp_path):
    from catanatron.colonist.ranked_runner import next_queue

    assert next_queue("ranked", None, tmp_path) == "ranked"
    assert next_queue("casual", None, tmp_path) == "casual"
    assert next_queue("casual", 10, tmp_path, "bot2") == "casual"  # no games yet
    rows = [{"queue": "casual", "account": "bot2"}] * 9 + [{"queue": "ranked"}]
    (tmp_path / "games.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert next_queue("casual", 10, tmp_path, "bot2") == "casual"
    with open(tmp_path / "games.jsonl", "a") as f:
        f.write(json.dumps({"queue": "casual", "account": "bot2"}) + "\n")
    assert next_queue("casual", 10, tmp_path, "bot2") == "ranked"


def test_ranked_unlock_is_read_from_the_home_page():
    from catanatron.colonist.ranked_runner import ranked_locked_from_text

    home = (
        "Ericha8499\nBots\nCasual\nRanked\n1v1\n2/5 Casual games to unlock\nStart Game"
    )
    assert ranked_locked_from_text(home) is True
    unlocked = "Ericha8499\nBots\nCasual\nRanked\n1v1\n10\n2\nStart Game"
    assert ranked_locked_from_text(unlocked) is False
    assert ranked_locked_from_text("Loading...") is None
