import json
import os
import time

from catanatron.colonist.spectator_publish import parse_names, snapshot, training


def _account(folder, pid, games=(), live=None, age=0):
    folder.mkdir()
    (folder / "runner-status.json").write_text(
        json.dumps({"state": "running", "pid": pid, "label": "g1 round 11"})
    )
    (folder / "games.jsonl").write_text("".join(json.dumps(g) + "\n" for g in games))
    if live is not None:
        path = folder / "spectator-state.json"
        path.write_text(json.dumps(live))
        os.utime(path, (time.time() - age, time.time() - age))


def test_parse_names():
    assert parse_names("ranked=CatBot77, bot2=Ericha8499,bad,") == {
        "ranked": "CatBot77",
        "bot2": "Ericha8499",
    }


def test_snapshot_reports_live_game_and_record(tmp_path):
    now = time.time()
    games = [
        {"winner": "BLUE", "bot_color": "BLUE", "queue": "ranked", "at": now},
        {"winner": "RED", "bot_color": "BLUE", "queue": "ranked", "at": now - 9e5},
        {"winner": "RED", "bot_color": "BLUE", "queue": "casual", "at": now},
        {"winner": "RED", "bot_color": "BLUE", "queue": "ranked", "own_bot": True},
    ]
    live = {"schema": 1, "live": {"phase": "live"}, "colonistGame": "city871"}
    _account(tmp_path / "ranked", os.getpid(), games, live)
    _account(tmp_path / "bot2", os.getpid(), live=live, age=600)  # stale
    _account(tmp_path / "bot3", 2**22 + 12345)  # process gone
    _account(tmp_path / "unnamed", os.getpid())
    (tmp_path / "bot2" / "NEEDS_OWNER.md").write_text("login prompt")

    body = snapshot(tmp_path, {"ranked": "A", "bot2": "B", "bot3": "C"}, "vm1")

    assert body["schema"] == 2 and body["source"] == "vm1"
    bots = {b["name"]: b for b in body["bots"]}
    assert set(bots) == {"A", "B", "C"}
    assert bots["A"]["status"] == "playing" and bots["A"]["gameId"] == "city871"
    assert bots["A"]["ranked"] == {"wins": 1, "losses": 1}
    assert bots["A"]["today"] == {"wins": 1, "losses": 1}
    assert bots["A"]["recent"] == "WLL"
    assert bots["A"]["queue"] == "ranked" and bots["A"]["net"] == "g1 round 11"
    assert bots["B"]["status"] == "paused" and bots["B"]["gameId"] is None
    assert bots["C"]["status"] == "offline"
    assert "opponent" not in json.dumps(body)


def test_finished_game_has_no_link(tmp_path):
    live = {"live": {"phase": "finished"}, "colonistGame": "city871"}
    _account(tmp_path / "ranked", os.getpid(), live=live)
    (bot,) = snapshot(tmp_path, {"ranked": "A"}, "vm1")["bots"]
    assert bot["status"] == "searching" and bot["gameId"] is None


def test_training_progress_and_records_by_net(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    history = [
        {
            "round": 5,
            "games": 400,
            "score": 0.55,
            "promoted": True,
            "benchmark": "0.93",
            "gauntlet": {"x/round_016.npz": 0.66},
            "live": {"mse": 0.6},
        },
        {"round": 6, "games": 2000, "score": 0.4795, "promoted": True, "benchmark": ""},
        {"round": 7, "games": 700, "score": 0.49, "promoted": False},
    ]
    (run / "state.json").write_text(
        json.dumps(
            {
                "round": 8,
                "step": "match",
                "best_round": 5,
                "history": history,
                "rollbacks": [{"from": 6, "to": 5}],
            }
        )
    )
    rows = [
        {
            "winner": "BLUE",
            "bot_color": "BLUE",
            "queue": "ranked",
            "round": 5,
            "label": "run round 5",
        },
        {
            "winner": "RED",
            "bot_color": "BLUE",
            "queue": "ranked",
            "round": 5,
            "label": "run round 5",
        },
        {
            "winner": "RED",
            "bot_color": "BLUE",
            "queue": "casual",
            "round": 5,
            "label": "run round 5",
        },
        {
            "winner": "BLUE",
            "bot_color": "BLUE",
            "queue": "ranked",
            "round": 6,
            "label": "run round 6",
        },
        {
            "winner": "BLUE",
            "bot_color": "BLUE",
            "queue": "ranked",
            "round": 6,
            "label": "sp1 round 6",
        },
    ]
    _account(tmp_path / "data", os.getpid())
    (tmp_path / "data" / "ranked").mkdir()
    (tmp_path / "data" / "ranked" / "runner-status.json").write_text("{}")
    (tmp_path / "data" / "ranked" / "games.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows)
    )

    body = snapshot(tmp_path / "data", {"ranked": "A"}, "vm1", run=run)

    t = body["training"]
    assert (t["round"], t["step"], t["bestRound"]) == (8, "match", 5)
    first, second, third = t["rounds"]
    assert first["original"] == 0.66 and first["benchmark"] == 0.93
    assert first["liveError"] == 0.6 and not first["rolledBack"]
    assert second["rolledBack"] and second["benchmark"] is None
    assert not third["promoted"] and not third["rolledBack"]
    assert body["netRecords"] == [
        {"round": 5, "wins": 1, "losses": 1},
        {"round": 6, "wins": 1, "losses": 0},
    ]
    assert training(tmp_path / "missing") is None
