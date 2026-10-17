"""The Discord orchestrator's schedule, budget and output parsing
(scripts/vm/orchestrator/core.py and agent.py)."""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/vm/orchestrator"))
import agent  # noqa: E402
import core  # noqa: E402


def toronto(hour, minute=0, day=16):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=core.TORONTO)


def kinds(when):
    return [kind for kind, _ in core.due(when)]


def test_major_reports_start_early_to_land_on_the_hour():
    assert kinds(toronto(8, 30)) == []
    assert kinds(toronto(8, 35)) == ["major"]
    assert core.due(toronto(8, 35)) == [("major", "2026-10-16 09")]
    assert kinds(toronto(9, 20)) == ["major"]
    assert kinds(toronto(14, 50)) == ["hourly", "major"]
    assert core.due(toronto(20, 40))[-1] == ("major", "2026-10-16 21")


def test_hourly_status_only_between_the_major_reports():
    assert kinds(toronto(10, 0)) == ["hourly"]
    assert kinds(toronto(15, 30)) == ["major"]  # no hourly; the key runs once
    assert kinds(toronto(20, 10)) == ["hourly"]
    assert kinds(toronto(21, 40)) == []
    assert kinds(toronto(3, 0)) == []


def test_the_schedule_follows_toronto_across_utc():
    nine_am = toronto(8, 40).astimezone(dt.timezone.utc)
    assert core.due(nine_am) == [("major", "2026-10-16 09")]


def test_quiet_hours():
    assert core.daytime(toronto(9, 0))
    assert core.daytime(toronto(21, 30))  # the 9 pm report's decisions still post
    assert not core.daytime(toronto(22, 0))
    assert not core.daytime(toronto(8, 59))


def test_spend_counts_the_toronto_day_and_workstream():
    rows = [
        {"day": "2026-10-16", "workstream": "training", "cost_usd": 1.5},
        {"day": "2026-10-16", "workstream": "efficiency", "cost_usd": 2},
        {"day": "2026-10-15", "workstream": "training", "cost_usd": 9},
        {"day": "2026-10-16", "workstream": None, "cost_usd": 0.5},
    ]
    late = toronto(23, 30)  # already Oct 17 in UTC
    assert core.spent(rows, late) == 4.0
    assert core.spent(rows, late, "training") == 1.5
    assert core.worker_budget(15, 0.3, ["a", "b", "c"]) == pytest.approx(3.5)


def start(**overrides):
    now = toronto(12)
    args = dict(
        now=now,
        running=0,
        max_workers=1,
        paused=False,
        spent_today=0,
        budget=3,
        rest_until=None,
        claude_blocked_until=None,
        codex_fallback=True,
    )
    args.update(overrides)
    return core.may_start(**args)


def test_when_a_workstream_may_start():
    assert start() == (True, "")
    assert start(paused=True) == (False, "paused")
    assert start(running=1) == (False, "worker slots full")
    assert start(spent_today=3) == (False, "daily budget used")
    assert start(rest_until=toronto(13)) == (False, "resting")
    assert start(rest_until=toronto(11)) == (True, "")
    blocked = dict(claude_blocked_until=toronto(13))
    assert start(**blocked) == (True, "")  # Codex takes over
    assert start(codex_fallback=False, **blocked) == (False, "Claude usage limit")


def test_cooldowns():
    ended = toronto(12)
    assert core.cooldown_until(ended, "progress") == toronto(12, 2)
    assert core.cooldown_until(ended, "idle") == toronto(15)
    assert core.cooldown_until(ended, "nonsense") == toronto(12, 30)


def test_chunks_fit_discord_and_keep_everything():
    text = "".join(f"line {i} " + "x" * 50 + "\n" for i in range(100)) + "y" * 4500
    parts = core.chunks(text)
    assert all(len(p) <= 1990 for p in parts)
    assert "".join(parts) == text


def test_final_json_takes_the_last_block():
    text = 'thinking {"a": 1}\n```json\n{"status": "idle", "post": "x {y}"}\n```\nbye'
    assert agent.final_json(text) == {"status": "idle", "post": "x {y}"}
    assert agent.final_json('first {"a": 1} then {"b": {"c": 2}}') == {"b": {"c": 2}}
    assert agent.final_json("no json here") is None


def test_outputs_become_decisions_and_inbox_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "STATE", tmp_path)
    (tmp_path / "workstreams/training").mkdir(parents=True)
    data = {
        "decisions": [
            {"id": "buy-max", "question": "Upgrade?", "options": ["Yes", "No"]},
            {"id": "buy-max", "question": "Again?", "urgent": True},
            {"question": "no id given"},
            "not a decision",
        ],
        "messages": {"training": "try lr 1e-3", "unknown": "dropped"},
    }
    agent.apply_outputs(data, "lead")
    rows = core.read_jsonl(tmp_path / "decisions.jsonl")
    assert [r["id"] for r in rows] == ["buy-max", "buy-max-2", "no-id-given"]
    assert rows[1]["urgent"] and rows[0]["from"] == "lead"
    assert "try lr 1e-3" in (tmp_path / "workstreams/training/inbox.md").read_text()
    assert not (tmp_path / "workstreams/unknown").exists()
    json.dumps(rows)


def test_workers_may_land_but_the_lead_may_not_push():
    lead, worker = agent.allowed_tools("lead"), agent.allowed_tools("worker")
    assert "Bash(scripts/vm/orchestrator/land.sh:*)" in worker
    assert not any("push" in t or "land.sh" in t for t in lead)
    assert not any("bridge" in t or "ranked_runner" in t for t in worker)


def test_a_worker_sees_its_open_questions(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "STATE", tmp_path)
    asked = {
        "decisions": [{"id": "a", "question": "A?"}, {"id": "b", "question": "B?"}]
    }
    assert agent.apply_outputs(asked, "efficiency") == 2
    assert agent.apply_outputs({"status": "progress"}, "efficiency") == 0
    (tmp_path / "answers.jsonl").write_text('{"id": "a", "answer": "yes"}\n')
    assert [d["id"] for d in agent.open_decisions("efficiency")] == ["b"]
    assert agent.open_decisions("training") == []


def test_rank_watch_finds_each_number_one():
    import rank_watch

    standing = [
        {"name": {"key": "strings:leaderboardPage.tabs.global"}, "rank": "65"},
        {"name": {"key": "strings:leaderboardPage.tabs.continents.NA"}, "rank": "1"},
        {"name": "CA", "rank": "4"},
        {"name": "Ontario", "rank": "1"},
    ]
    assert rank_watch.firsts(standing) == ["North America", "Ontario"]
    assert rank_watch.firsts(None) == []
    assert rank_watch.level_name(standing[0]) == "Global"


def test_rank_watch_queues_once_and_releases_by_day(tmp_path, monkeypatch):
    import rank_watch

    monkeypatch.setattr(rank_watch, "STATE", tmp_path)
    monkeypatch.setattr(rank_watch, "SHOTS", tmp_path / "shots")
    monkeypatch.setattr(rank_watch, "accounts", lambda: ["Petra4620"])
    pages = {
        "ranked-seasons": [{"seasonType": 20}, {"seasonType": 21}],
        "Petra4620/ranked/21": [
            {
                "modeType": 1,
                "skillRating": 2400,
                "rankedStanding": [{"name": "Ontario", "rank": "1"}],
            }
        ],
    }
    monkeypatch.setattr(
        rank_watch, "fetch", lambda url: pages[url.split("/profile/")[1]]
    )
    shots = []
    monkeypatch.setattr(
        rank_watch, "screenshot", lambda name, path: shots.append(path) or path.touch()
    )
    rank_watch.main([])  # night: shoot now, post later
    rank_watch.main([])  # already seen: no second shot
    assert len(shots) == 1
    state = json.loads((tmp_path / "rank-watch.json").read_text())
    assert len(state["queue"]) == 1 and "Ontario" in state["queue"][0]["text"]
    rank_watch.main(["--release"])
    assert json.loads((tmp_path / "rank-watch.json").read_text())["queue"] == []
