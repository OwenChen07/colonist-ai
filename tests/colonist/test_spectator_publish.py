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


def test_round_progress_recognizes_aux_loss_after_cpu_fallback():
    lines = """[12:44:32] round 27: training on 10 data folders, from the best net
[12:44:53] round 27: training on a GPU (cuda)
epoch 1: train policy 0.911 value 0.403 | val policy 0.901 value 0.429 accuracy 76.7% aux 0.324 | 201 s
[12:53:48] round 27: training on the CPU after GPU timeout
epoch 1: train policy 0.911 value 0.403 | val policy 0.901 value 0.430 accuracy 76.8% aux 0.323 | 2352 s
epoch 2: train policy 0.902 value 0.399 | val policy 0.896 value 0.429 accuracy 77.2% aux 0.319 | 2342 s
""".splitlines()
    now = dt.datetime(2026, 10, 13, 14, 34, tzinfo=dt.timezone.utc).timestamp()
    progress = round_progress(lines, 27, now=now)
    assert progress["training"]["started"] == "2026-10-13T12:53:48+00:00"
    assert [e["epoch"] for e in progress["training"]["epochs"]] == [1, 2]
    assert progress["training"]["epochs"][1]["accuracy"] == 0.772


def test_round_progress_counts_local_fallback_games():
    lines = """[16:22:12] round 28: self-play, 3500 games
[16:25:25]   self-play: 832 games here
[17:09:02]   shard_3028000768_64.npz: 64 games in 1360 s | 832 games, 0.32/s
[17:09:15]   self-play: 1004 additional games here
[17:47:19]   shard_3028003456_44.npz: 44 games in 888 s | 556 games, 0.24/s
[17:55:15]   shard_3028003264_64.npz: 64 games in 1416 s | 1004 games, 0.36/s
""".splitlines()
    now = dt.datetime(2026, 10, 13, 18, 0, tzinfo=dt.timezone.utc).timestamp()
    progress = round_progress(lines, 28, now=now)["selfplay"]
    assert progress["localTotal"] == 1836
    assert progress["localDone"] == 1836


def test_round_progress_counts_unprepared_remote_range():
    lines = """[16:22:12] round 28: self-play, 3500 games
[16:22:14]   self-play: 448 games (seeds 3028000832+) on worker-a
[16:22:33]   self-play: 448 games (seeds 3028001280+) on worker-b
[16:22:51]   self-play: 192 games (seeds 3028001728+) on worker-c
[16:22:59]   self-play: 192 games (seeds 3028001920+) on worker-d
[16:23:06]   self-play: 192 games (seeds 3028002112+) on worker-e
[16:23:12]   self-play: 192 games (seeds 3028002304+) on worker-f
[16:25:25]   could not prepare worker-g; its range runs here
[16:25:25]   self-play: 832 games here
[16:25:25]   13 shards to play
[17:09:02]   shard_3028000768_64.npz: 64 games in 1360 s | 832 games, 0.32/s
[17:09:15]   16 shards to play
[17:55:15]   shard_3028003264_64.npz: 64 games in 1416 s | 1004 games, 0.36/s
""".splitlines()
    now = dt.datetime(2026, 10, 13, 18, 0, tzinfo=dt.timezone.utc).timestamp()
    progress = round_progress(lines, 28, now=now)["selfplay"]
    assert progress["localTotal"] == 1836
    assert progress["localDone"] == 1836


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


def test_training_shows_the_configured_epochs_and_every_round(tmp_path):
    history = [{"round": r, "games": 1000, "score": 0.5} for r in range(1, 58)]
    (tmp_path / "state.json").write_text(
        json.dumps({"round": 58, "step": "train", "best_round": 40, "history": history})
    )
    assert training(tmp_path)["epochs"] == 3  # no config yet: train.sh's default
    (tmp_path / "train-config.json").write_text('{"epochs": 4, "games": 5000}')
    t = training(tmp_path)
    assert t["epochs"] == 4 and len(t["rounds"]) == 57
    (tmp_path / "train-tail.log").write_text(
        "[20:00:00] round 58: training on 10 data folders, from the best net\n"
        "epoch 1: train policy 0.9 value 0.4 | val policy 0.9 value 0.4 accuracy 77.0% | 200 s\n"
    )
    assert training(tmp_path)["progress"]["training"]["total"] == 4


def test_round_progress_with_early_self_play():
    """Round 62's lines from the live log: its early self-play was announced
    during round 61, and round 63's during round 62's training."""
    lines = [
        "[08:03:20] round 62: self-play started early, alongside round 61",
        "[08:03:21] round 61: training on 10 data folders, from round 60's net",
        "[08:19:11] round 61: promotion match (confidence gate), new vs best (at most 2000 games)",
        "[10:53:01] round 61: live value MSE 0.593, picks the winner 77.2%",
        "[10:53:01] round 62: waiting for its early self-play to finish",
        "[11:03:01] round 62: early self-play still running; stopping it",
        "[11:03:02] round 62: before the round: scripts/vm/human_dataset.sh",
        "[11:04:54] round 62: self-play by round 61's net (it scored 51.0%), 5000 games, opponents []",
        "[11:05:53]   1 shards to play",
        "[11:40:00]   shard_3062000000_64.npz: 64 games in 900 s | 64 games, 0.07/s",
        "[12:15:00]   self-play complete across 8 machines: 5000 games",
        "[12:16:04] round 63: self-play started early, alongside round 62",
        "[12:16:05] round 62: training on 10 data folders, from round 61's net",
        "epoch 1: train policy 0.83 value 0.38 | val policy 0.83 value 0.40 accuracy 79.2% aux 0.3 | 212 s",
        "[12:31:31] round 62: promotion match (confidence gate), new vs best (at most 2000 games)",
        "[13:00:00]   1000 games: 51.1% ± 2.9%",
    ]
    now = dt.datetime(2026, 10, 16, 13, 5, tzinfo=dt.timezone.utc).timestamp()
    p = round_progress(lines, 62, epochs=4, now=now)
    assert p["selfplay"]["games"] == 5000 and p["selfplay"]["done"]
    assert p["selfplay"]["localDone"] == 64
    assert p["training"]["total"] == 4 and len(p["training"]["epochs"]) == 1
    assert p["match"]["games"] == 1000
