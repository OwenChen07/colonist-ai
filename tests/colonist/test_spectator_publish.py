import datetime as dt
import json
import os
import time

from catanatron.colonist.spectator_publish import (
    parse_names,
    round_progress,
    snapshot,
    training,
)


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
    assert bots["A"]["today"] == {"wins": 1, "losses": 0}  # casual left out
    assert bots["A"]["recent"] == "WL"
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


LOG = """[14:52:39] round 12: self-play, 5000 games, opponents [], setup noise scale 1
[14:53:28]   self-play: 1728 games here
[15:00:01]   shard_1_64.npz: 64 games in 737 s | 64 games, 0.26/s
[15:43:52]   self-play complete across 7 machines: 5000 games
[15:43:52] round 12: training on 12 data folders, from the best net
epoch 1: train policy 0.984 value 0.401 | val policy 0.991 value 0.417 accuracy 74.1% | 2891 s
epoch 2: train policy 0.981 value 0.399 | val policy 0.982 value 0.415 accuracy 74.7% | 2770 s
[18:05:14] round 12: promotion match (loose gate), new vs best (at most 2000 games)
[18:07:23]   100 games: 54.0% ± 8.3%
[18:09:14]   200 games: 49.5% ± 6.2%
[18:31:41] round 13: self-play, 4000 games, opponents [], setup noise scale 1
[18:31:43]   self-play: 1000 games here
[18:40:00]   shard_2_64.npz: 64 games in 700 s | 128 games, 0.3/s
""".splitlines()


def test_round_progress_from_the_training_log():
    now = dt.datetime(2026, 10, 11, 19, 0, tzinfo=dt.timezone.utc).timestamp()
    twelve = round_progress(LOG, 12, now=now)
    assert twelve["selfplay"]["done"] and twelve["selfplay"]["localDone"] == 64
    assert twelve["training"]["started"] == "2026-10-11T15:43:52+00:00"
    assert [e["epoch"] for e in twelve["training"]["epochs"]] == [1, 2]
    assert twelve["training"]["epochs"][1]["accuracy"] == 0.747
    assert twelve["match"] == {
        "started": "2026-10-11T18:05:14+00:00",
        "maxGames": 2000,
        "games": 200,
        "score": 0.495,
        "margin": 0.062,
    }
    thirteen = round_progress(LOG, 13, now=now)
    assert thirteen["selfplay"] == {
        "started": "2026-10-11T18:31:41+00:00",
        "games": 4000,
        "done": False,
        "localDone": 128,
        "localTotal": 1000,
    }
    assert thirteen["training"] is None and thirteen["match"] is None
    assert round_progress(LOG, 14, now=now) is None
    # Just after midnight UTC, a late-evening stamp is from the day before.
    early = dt.datetime(2026, 10, 12, 0, 5, tzinfo=dt.timezone.utc).timestamp()
    assert round_progress(LOG, 13, now=early)["selfplay"]["started"].startswith(
        "2026-10-11T18:31"
    )


def test_credits_are_published_when_present(tmp_path):
    assert snapshot(tmp_path, {}, "vm1")["credits"] is None
    (tmp_path / "credits.json").write_text(
        json.dumps(
            {
                "credits": [
                    {
                        "provider": "Azure",
                        "remaining": 74.314,
                        "total": 100.0,
                        "asOf": "2026-10-12T00:53:09+00:00",
                        "source": "api",
                        "extra": "x",
                    },
                    {
                        "provider": "GCP",
                        "remaining": None,
                        "total": 425,
                        "asOf": None,
                        "source": "console",
                        "currency": "CAD",
                    },
                ]
            }
        )
    )
    azure, gcp = snapshot(tmp_path, {}, "vm1")["credits"]
    assert azure == {
        "provider": "Azure",
        "remaining": 74.314,
        "total": 100.0,
        "asOf": "2026-10-12T00:53:09+00:00",
        "source": "api",
        "currency": "USD",
    }
    assert gcp["remaining"] is None and gcp["source"] == "console"
    assert gcp["currency"] == "CAD"


def test_retired_accounts_keep_their_record_and_show_nothing_live(tmp_path):
    now = time.time()
    games = [{"winner": "BLUE", "bot_color": "BLUE", "queue": "ranked", "at": now}]
    live = {"schema": 1, "live": {"phase": "live"}, "colonistGame": "city871"}
    _account(tmp_path / "ranked", os.getpid(), games, live)
    (tmp_path / "ranked" / "NEEDS_OWNER.md").write_text("can't queue")
    _account(tmp_path / "bot5", os.getpid())
    body = snapshot(
        tmp_path, {"bot5": "P"}, "vm1", retired={"ranked": "A", "bot2": "gone"}
    )
    bots = {b["name"]: b for b in body["bots"]}
    assert set(bots) == {"A", "P"}  # no folder, no card
    assert bots["A"]["status"] == "retired" and bots["A"]["gameId"] is None
    assert bots["A"]["ranked"] == {"wins": 1, "losses": 0}
    assert bots["A"]["recent"] == "W" and bots["A"]["net"] == "g1 round 11"


def test_orchestrator_report_is_published_as_plain_fields(tmp_path):
    from catanatron.colonist.spectator_publish import orchestrator

    assert snapshot(tmp_path, {}, "vm1")["orchestrator"] is None
    (tmp_path / "orchestrator.json").write_text(
        json.dumps(
            {
                "at": "2026-10-13T04:00:00+00:00",
                "summary": "Round 23 kept.",
                "questions": [
                    {"id": "gpu-hours", "text": "OK to run the PC overnight?"},
                    {"id": "Bad Id!", "text": "dropped"},
                ],
                "extra": "ignored",
            }
        )
    )
    report = orchestrator(tmp_path)
    assert report["summary"] == "Round 23 kept."
    assert report["questions"] == [
        {"id": "gpu-hours", "text": "OK to run the PC overnight?"}
    ]
    assert set(report) == {"at", "summary", "questions"}
