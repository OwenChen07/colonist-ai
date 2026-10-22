import asyncio
import json
import os
import time

from catanatron.colonist import own_queue
from catanatron.colonist.spectator_publish import bot_status

OWN = {"Amy", "Bob", "Cat", "Dan"}
NAMES = {"bot3": "Bob", "bot4": "Cat"}


def _runner(folder, **fields):
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"state": "running", "pid": os.getpid(), **fields}
    (folder / "runner-status.json").write_text(json.dumps(doc))


def _live(folder):
    (folder / "spectator-state.json").write_text(
        json.dumps({"colonistGame": "abc123", "live": {"phase": "live"}})
    )


def _alive(pid):
    return pid == os.getpid()


def test_who_an_account_waits_for():
    states = {
        "Amy": "waiting",  # earlier name, waiting: it queues first
        "Bob": "queued",
        "Dan": "waiting",  # later name: waits for us
        "Cat": "queued",  # ourselves
        "Eve": "queued",  # not ours
    }
    assert own_queue.blockers(states, "Cat", OWN) == ["Amy", "Bob"]
    assert own_queue.blockers({"Dan": "other"}, "Cat", OWN) == []


def test_the_site_shows_queued_and_waiting_accounts():
    bots = [
        {"name": "Amy", "status": "searching", "queue": "ranked"},
        {"name": "Bob", "status": "searching", "queue": None},
        {"name": "Cat", "status": "playing", "queue": "ranked"},
        {"name": "Dan", "status": "searching", "queue": "casual"},
    ]
    assert own_queue.site_states(bots) == {
        "Amy": "queued",
        "Bob": "waiting",
        "Cat": "other",
        "Dan": "other",
    }


def test_a_name_shown_twice_reads_its_live_entry():
    # VM 3 posted under two hostnames: one card offline, one current.
    bots = [
        {"name": "Amy", "status": "searching", "queue": "ranked"},
        {"name": "Amy", "status": "offline", "queue": None},
        {"name": "Bob", "status": "offline", "queue": None},
        {"name": "Bob", "status": "searching", "queue": None},
        {"name": "Cat", "status": "playing", "updatedAt": "2026-10-22T16:00:00Z"},
        {"name": "Cat", "status": "searching", "queue": "ranked"},
        {"name": "Cat", "status": "searching", "updatedAt": "2026-10-22T16:05:00Z"},
    ]
    assert own_queue.site_states(bots) == {
        "Amy": "queued",
        "Bob": "waiting",
        "Cat": "waiting",
    }


def test_the_wait_is_on_unless_turned_off():
    assert own_queue.site_setting(None) == own_queue.SITE
    assert own_queue.site_setting("https://x.example/") == "https://x.example/"
    assert own_queue.site_setting("0") is None
    assert own_queue.site_setting("off") is None
    assert own_queue.site_setting("") is None


def test_this_vms_folders_override_the_site(tmp_path):
    _runner(tmp_path / "bot3", queue_state="queued")
    _runner(tmp_path / "bot4", queue_state="queued")
    _live(tmp_path / "bot4")  # matched: in a game, not in the queue

    def site(_):
        return [
            {"name": "Bob", "status": "searching", "queue": None},  # lagging
            {"name": "Amy", "status": "searching", "queue": "ranked"},
        ]

    states = own_queue.current_states("x", tmp_path / "bot4", NAMES, site, _alive)
    assert states == {"Amy": "queued", "Bob": "queued", "Cat": "other"}


def test_an_unreadable_site_counts_as_nobody_queued(tmp_path):
    def broken(_):
        raise OSError("down")

    assert own_queue.current_states("x", tmp_path / "bot4", {}, broken) == {}


def _wait(tmp_path, answers, max_wait=90):
    clock = [0.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def fetch(_):
        return answers.pop(0) if answers else []

    _runner(tmp_path / "bot4")
    waited = asyncio.run(
        own_queue.wait_for_own_accounts(
            "x",
            tmp_path / "bot4",
            "Cat",
            OWN,
            {"bot4": "Cat"},
            fetch=fetch,
            alive=_alive,
            sleep=sleep,
            clock=lambda: clock[0],
            jitter=5,
            max_wait=max_wait,
        )
    )
    return waited, sleeps


def test_an_account_waits_until_ours_is_in_a_game(tmp_path):
    queued = [{"name": "Amy", "status": "searching", "queue": "ranked"}]
    playing = [{"name": "Amy", "status": "playing", "queue": "ranked"}]
    waited, sleeps = _wait(tmp_path, [queued, queued, playing])
    assert sleeps == [5, 12, 12]
    assert waited == 29
    status = json.loads((tmp_path / "bot4" / "runner-status.json").read_text())
    assert status["queue_state"] == "waiting" and status["state"] == "running"
    own_queue.mark_queued(tmp_path / "bot4")
    status = json.loads((tmp_path / "bot4" / "runner-status.json").read_text())
    assert status["queue_state"] == "queued" and status["pid"] == os.getpid()


def test_an_account_queues_after_90_seconds_at_most(tmp_path):
    queued = [{"name": "Amy", "status": "searching", "queue": "ranked"}]
    waited, _ = _wait(tmp_path, [queued] * 20)
    assert 90 <= waited < 90 + 12


def test_nobody_queued_means_no_wait_beyond_the_jitter(tmp_path):
    assert _wait(tmp_path, [[]]) == (5, [5])


def test_a_waiting_account_is_published_without_a_queue(tmp_path):
    folder = tmp_path / "bot4"
    _runner(folder, queue_state="waiting")
    (folder / "games.jsonl").write_text(
        json.dumps({"queue": "ranked", "winner": "RED", "bot_color": "RED"}) + "\n"
    )
    now = time.time()
    assert bot_status(folder, "Cat", now)["queue"] is None
    _runner(folder, queue_state="queued")
    assert bot_status(folder, "Cat", now)["queue"] == "ranked"
