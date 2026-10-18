"""The Discord orchestrator's schedule, budget and output parsing
(scripts/vm/orchestrator/core.py and agent.py)."""

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
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
    # the plan's percentages, when known, gate instead of the dollars
    plan = {"five_hour": 40.0, "seven_day": 60.0}
    assert start(plan=plan, spent_today=3) == (True, "")
    for over in ({"five_hour": 86.0}, {"seven_day": 91.0}):
        assert start(plan={**plan, **over}) == (False, "plan nearly used")
    assert start(plan={**plan, "five_hour": 86.0}, plan_5h_max=95) == (True, "")


def test_plan_usage(tmp_path, monkeypatch):
    import plan_usage

    creds = tmp_path / "creds.json"
    assert plan_usage.token(creds) is None
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "expiresAt": 2000}})
    )
    assert plan_usage.token(creds, now=1.0) == "t"
    assert plan_usage.token(creds, now=3.0) is None  # expired

    data = {
        "five_hour": {"utilization": 42.4, "resets_at": "2026-10-17T23:00:00Z"},
        "seven_day": {"utilization": 61, "resets_at": None},
        "seven_day_opus": None,
    }
    plan = plan_usage.parse(data)
    assert plan["five_hour"] == 42.4 and plan["seven_day"] == 61.0
    assert plan_usage.line(plan) == "5 h: 42%, week: 61%"
    assert plan_usage.parse({"five_hour": None, "seven_day": {}}) is None
    assert plan_usage.parse("nonsense") is None
    assert plan_usage.line(None) == ""

    monkeypatch.setenv("ORCHESTRATOR_CLAUDE_CREDENTIALS", str(tmp_path / "none"))
    assert plan_usage.fetch() is None  # no token: no request

    calls = []
    monkeypatch.setattr(plan_usage, "fetch", lambda: calls.append(1) or plan)
    monkeypatch.setattr(plan_usage, "_cache", {"at": None, "value": None})
    t = [100.0]
    assert plan_usage.usage(lambda: t[0]) == plan
    t[0] = 159.0
    plan_usage.usage(lambda: t[0])
    assert len(calls) == 1  # cached for 60 s
    t[0] = 160.0
    plan_usage.usage(lambda: t[0])
    assert len(calls) == 2


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


def test_the_lead_and_workers_land_but_never_push():
    lead, worker = agent.allowed_tools("lead"), agent.allowed_tools("worker")
    # The owner's decision of Oct 17: the lead gets the workers' commands,
    # plus the bot restart (test_only_the_lead_may_restart_the_bot).
    assert [t for t in lead if "restart_bot" not in t] == worker
    assert "Bash(scripts/vm/orchestrator/land.sh:*)" in worker
    assert not any("push" in t for t in worker)
    assert not any("bridge" in t or "ranked_runner" in t for t in worker)
    for tools in (lead, worker):
        for script in ("kill_landing.sh", "kill_job.sh"):
            assert f"Bash(scripts/vm/orchestrator/{script}:*)" in tools
            assert (
                f"Bash({agent.HOME_ROOT}/scripts/vm/orchestrator/{script}:*)" in tools
            )


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


def test_decision_options_fit_a_phone():
    options = [
        "Recommended: allow Bash for worker sessions in agent.py",
        "Keep it as is",
    ]
    assert core.option_lines(options) == [
        "**1 ★.** allow Bash for worker sessions in agent.py",
        "**2.** Keep it as is",
    ]
    assert [core.button_label(i, o) for i, o in enumerate(options)] == ["1 ★", "2"]
    assert core.chosen(options, " 2 ") == "Keep it as is"
    assert core.chosen(options, "#1") == options[0]
    assert core.chosen(options, "option 2") == "Keep it as is"
    assert core.chosen(options, "3") is None
    assert core.chosen(options, "2 but only for training") is None


def test_permission_requests_never_reach_the_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "STATE", tmp_path)
    data = {
        "decisions": [
            {"id": "bash", "question": "Bash is denied for the worker. Allow it?"},
            {"id": "rules", "question": "Can the allow list include git clean?"},
            {"id": "real", "question": "Deploy E15 to all accounts tonight?"},
        ],
        "blocked_commands": ["git clean -f tests/tmp.py"],
    }
    assert agent.apply_outputs(data, "efficiency") == 1
    assert [d["id"] for d in core.read_jsonl(tmp_path / "decisions.jsonl")] == ["real"]
    log = (tmp_path / "permission-requests.md").read_text()
    assert "git clean -f" in log and "Bash is denied" in log and "E15" not in log


def test_workers_run_any_module_but_never_the_live_bots():
    worker, denied = agent.allowed_tools("worker"), agent.disallowed_tools("worker")
    assert "Bash(.venv/bin/python -m catanatron.colonist.*)" in worker
    assert "Bash(nohup .venv/bin/python -m catanatron.openhex.*)" in worker
    assert "Bash(.venv/bin/python data/orchestrator/scratch/*)" in worker
    assert "Bash(.venv/bin/python -m catanatron.colonist.bridge*)" in denied
    assert (
        "Bash(nohup .venv/bin/python -m catanatron.colonist.ranked_runner*)" in denied
    )
    assert any(t.startswith("Bash(~/") and "safe_restart_account" in t for t in worker)


def test_workers_manage_gcp_workers_but_not_the_machines_we_run_on():
    worker, denied = agent.allowed_tools("worker"), agent.disallowed_tools("worker")
    assert "Bash(gcloud compute *)" in worker
    assert "Bash(gcloud compute instances delete*colonist-selfplay-1*)" in denied
    assert "Bash(gcloud compute instances stop*colonist-selfplay-worker-4*)" in denied
    assert "Bash(gcloud iam *)" in denied
    assert agent.disallowed_tools("lead") == denied


FAKE_CLAUDE = """
import json, sys, time
assert sys.argv[1:] == ["--output-format", "stream-json", "--verbose"]
prompt = sys.stdin.read()
say = lambda text: {"type": "assistant", "message": {"content": [
    {"type": "text", "text": text}, {"type": "tool_use", "name": "Bash"}]}}
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
print(json.dumps(say("working on " + prompt)), flush=True)
if prompt == "slow":
    time.sleep(60)
print("a stderr line about a rate limit in some file", flush=True)
print(json.dumps({"type": "result", "result": "done", "session_id": "s1",
                  "total_cost_usd": 0.5}), flush=True)
if prompt == "watcher":
    print(json.dumps(say("the watcher fired")), flush=True)
    print(json.dumps({"type": "result", "result": "the watcher fired",
                      "session_id": "s1", "total_cost_usd": 0.6,
                      "origin": {"kind": "task-notification"}}), flush=True)
"""


def stream(tmp_path, prompt, timeout):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE)
    log = tmp_path / "run.log"
    command = [sys.executable, str(script), "--output-format", "json"]
    out = agent.stream_claude(
        command, prompt, cwd=tmp_path, env=None, log=log, timeout=timeout
    )
    return out, log.read_text()


def test_claude_streams_into_the_log_and_ends_with_its_result(tmp_path):
    (result, said, other, timed_out), log = stream(tmp_path, "fast", 30)
    assert not timed_out
    assert result["result"] == "done" and result["session_id"] == "s1"
    assert said == "working on fast"
    assert "rate limit" in other and '"type": "result"' in log


def test_a_background_watcher_s_wakeup_does_not_replace_the_real_result(tmp_path):
    # Oct 17: two Monitor wakeups after the final message made the last result
    # a one-liner, and the run was filed as "ended without a summary".
    (result, said, _, _), _ = stream(tmp_path, "watcher", 30)
    assert result["result"] == "done" and result["total_cost_usd"] == 0.5
    assert said.endswith("the watcher fired")


def test_a_timed_out_run_keeps_what_it_said(tmp_path):
    (result, said, _, timed_out), log = stream(tmp_path, "slow", 2)
    assert timed_out and result is None
    assert said == "working on slow"
    assert "working on slow" in log and log.endswith("(timed out)\n")


KILL_LANDING = (
    Path(__file__).resolve().parents[1] / "scripts/vm/orchestrator/kill_landing.sh"
)


def kill_landing(*args):
    return subprocess.run(
        ["bash", str(KILL_LANDING), *args], capture_output=True, text=True
    )


def test_kill_landing_kills_a_stuck_landing_and_nothing_else(tmp_path):
    lock = tmp_path / ".land.lock"
    lock.touch()
    # Another process holds the lock, so the fake land.sh waits in `flock 8`
    # with its "pytest" running. The holder is not a landing process: it is
    # double-forked out of this test's tree, which under land.sh is one.
    holder = int(
        subprocess.run(
            ["bash", "-c", f"flock {lock} sleep 60 >/dev/null 2>&1 & echo $!"],
            capture_output=True,
            text=True,
        ).stdout
    )

    def alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    land = tmp_path / "land.sh"
    land.write_text(
        'exec 8>"$1"\n'
        f"{sys.executable} -c 'import time; time.sleep(60)' -m pytest &\n"
        "flock -w 60 8\n"
    )
    landing = subprocess.Popen(["bash", str(land), str(lock)])
    # Under land.sh this test is itself a landing process: touch only our own.
    ours = (str(land), "flock -w 60 8", "time.sleep(60) -m pytest")

    def listed():
        lines = kill_landing().stdout.splitlines()
        return {
            int(l.split()[0]): l for l in lines if l.endswith(ours) or str(land) in l
        }

    try:
        for _ in range(100):
            if len(listed()) == 3:  # land.sh, its flock and its "pytest"
                break
            time.sleep(0.1)
        pids = listed()
        assert len(pids) == 3 and landing.pid in pids and holder not in pids
        refused = kill_landing("--yes", str(holder))
        assert refused.returncode == 1 and "refused" in refused.stdout
        killed = kill_landing("--yes", str(landing.pid)).stdout  # and what's under it
        assert all(f"killed {pid}" in killed for pid in pids)
        assert landing.wait(10) != 0
        assert alive(holder)
        assert listed() == {}
    finally:
        landing.kill()
        if alive(holder):
            os.kill(holder, signal.SIGTERM)


KILL_JOB = KILL_LANDING.with_name("kill_job.sh")


def kill_job(jobs, *args):
    return subprocess.run(
        ["bash", str(KILL_JOB), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "KILL_JOB_DIR": str(jobs)},
    )


def test_kill_job_kills_a_listed_job_with_its_children_and_nothing_else(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    # A job in its own process group with a child (its "ssh"), and a process
    # the job did not start.
    job = subprocess.Popen(
        ["bash", "-c", "sleep 61 & sleep 62 & wait"], start_new_session=True
    )
    other = subprocess.Popen(["sleep", "63"])
    (jobs / "e17.pid").write_text(f"{job.pid}\n")
    (jobs / "other.pid").write_text(f"{other.pid}\n")
    try:
        for _ in range(100):
            listed = kill_job(jobs, "e17").stdout
            if len(listed.splitlines()) == 4:  # the job, its 2 children, a note
                break
            time.sleep(0.1)
        assert str(job.pid) in listed and "listed only" in listed
        assert job.poll() is None  # listing kills nothing
        for refused in (["../e17"], ["nope", "--yes"]):
            assert kill_job(jobs, *refused).returncode != 0
        # A pid file written before its process started: a reused pid.
        os.utime(jobs / "other.pid", (time.time() - 3600,) * 2)
        stale = kill_job(jobs, "other", "--yes")
        assert stale.returncode == 1 and "reused" in stale.stdout
        started = time.time()
        killed = kill_job(jobs, "e17", "--yes").stdout
        assert killed.count("killed") == 3 and f"killed {job.pid}" in killed
        assert "forced" not in killed and time.time() - started < 8  # TERM was enough
        # Killed by its own signal, not exiting cleanly after its children died.
        assert job.wait(10) == -signal.SIGTERM
        assert "not running" in kill_job(jobs, "e17").stdout
        assert other.poll() is None
    finally:
        job.kill()
        other.kill()


FAKE_WORKER = """
import sys, time
from pathlib import Path
time.sleep(1)
d = Path(sys.argv[2])
(d / "runs" / "20261017-210000.md").write_text("round 71 took 41 min")
with open(d / "progress.md", "a") as notes:
    notes.write("\\n## Oct 17 21:00 (claude, idle)\\nnotes\\n")
"""


def test_a_restarted_bot_follows_its_workers_and_posts_what_they_left(
    tmp_path, monkeypatch
):
    import asyncio

    import discord_bot

    monkeypatch.setattr(discord_bot, "STATE", tmp_path)
    monkeypatch.setattr(discord_bot, "WORKSTREAMS", ["efficiency", "openhex"])
    monkeypatch.setattr(discord_bot, "FOLLOW_EVERY_S", 0.1)
    for name in ("efficiency", "openhex"):
        (tmp_path / "workstreams" / name / "runs").mkdir(parents=True)
    runs = tmp_path / "workstreams" / "openhex" / "runs"
    (runs / "20261017-180000.md").write_text("old, posted before")
    posts = []

    def bot():
        orchestrator = discord_bot.Orchestrator(1, 2)

        async def say(name, text, **kwargs):
            posts.append((name, text))

        orchestrator.say = say
        return orchestrator

    first = bot()  # the first start seeds what was posted
    assert first.state["last_run"] == {"openhex": "20261017-180000"}
    first.save()
    # Runs that ended while the bot was down; a -partial.md is never posted.
    (runs / "20261017-190000.md").write_text("orphaned post")
    (runs / "20261017-190000-partial.md").write_text("last words")
    script = tmp_path / "agent.py"  # its command line looks like a worker's
    script.write_text(FAKE_WORKER)
    d = tmp_path / "workstreams" / "efficiency"
    worker = subprocess.Popen([sys.executable, str(script), "worker", str(d)])
    first.state["workers"]["efficiency"] = {
        "pid": worker.pid,
        "started": "2026-10-18T00:50:00+00:00",
    }
    first.save()

    async def restart():
        again = bot()
        await again.recover()
        assert posts == [("openhex", "orphaned post")]
        assert list(again.running) == ["efficiency"]  # no second copy
        assert discord_bot.alive(worker.pid)
        await asyncio.wait_for(asyncio.gather(*again.tasks), 10)
        return again

    again = asyncio.run(restart())
    worker.wait()
    assert posts[1:] == [("efficiency", "round 71 took 41 min")]
    assert again.running == {} and again.state["workers"] == {}
    rest = again.state["rest"]["efficiency"]
    until = dt.datetime.fromisoformat(rest["until"]).timestamp()
    assert until - rest["ended"] == 3 * 3600  # idle: three hours, not two minutes
    assert not discord_bot.alive(worker.pid)


def test_only_the_lead_may_restart_the_bot():
    restart = "Bash(scripts/vm/orchestrator/restart_bot.sh:*)"
    assert restart in agent.allowed_tools("lead")
    assert restart not in agent.allowed_tools("worker")
    assert not any("sudo" in t for t in agent.allowed_tools("lead"))
