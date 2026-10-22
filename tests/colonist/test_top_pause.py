import asyncio
import json

from catanatron.colonist import top_pause


def _fetcher(rank, rating=2440, fail=False):
    calls = []

    def fetch(path):
        calls.append(path)
        if fail:
            raise OSError("down")
        if path == "profile/ranked-seasons":
            return [{"seasonType": 3}, {"seasonType": 4}, {"seasonType": None}]
        assert path == "profile/Amy/ranked/4"
        return [
            {"modeType": 0, "skillRating": "1500", "rankedStanding": [{"rank": "1"}]},
            {
                "modeType": 1,
                "skillRating": str(rating),
                "rankedStanding": [{"rank": str(rank)}, {"rank": "1"}],
            },
        ]

    fetch.calls = calls
    return fetch


class Sleeper:
    """Counts sleeps; on the nth, runs an action (the owner lifting the pause)."""

    def __init__(self, at=None, action=None):
        self.slept, self.at, self.action = [], at, action

    async def __call__(self, seconds):
        self.slept.append(seconds)
        if self.action and len(self.slept) == self.at:
            self.action()


def test_reads_the_global_1v1_rank():
    assert top_pause.read_rank("Amy", _fetcher(23)) == (23, 2440)
    assert top_pause.parse_standing([{"modeType": 0}]) == (None, None)
    assert top_pause.parse_standing([{"modeType": 1, "skillRating": "x"}]) == (
        None,
        None,
    )


def test_outside_the_top_ten_plays_on(tmp_path):
    sleep = Sleeper()
    watch = top_pause.Watch(tmp_path, "Amy", 10, _fetcher(23), sleep)
    asyncio.run(watch.check("round 208"))
    assert not (tmp_path / top_pause.PAUSE_FILE).exists()
    assert sleep.slept == [] and watch.last == 23


def test_near_the_line_reads_again_after_a_pause(tmp_path):
    sleep = Sleeper()
    watch = top_pause.Watch(tmp_path, "Amy", 10, _fetcher(12), sleep)
    asyncio.run(watch.check("round 208"))
    assert sleep.slept == [top_pause.SETTLE_SECONDS]
    assert not (tmp_path / top_pause.PAUSE_FILE).exists()


def test_top_ten_pauses_until_the_file_is_removed(tmp_path):
    (tmp_path / "runner-status.json").write_text(json.dumps({"state": "running"}))
    marker = tmp_path / top_pause.PAUSE_FILE
    sleep = Sleeper(at=3, action=marker.unlink)
    watch = top_pause.Watch(tmp_path, "Amy", 10, _fetcher(9, 2471), sleep)

    seen = {}

    async def run():
        task = asyncio.ensure_future(watch.check("E54 step 50"))
        await asyncio.sleep(0)
        await task

    original = sleep.action

    def lift():
        seen["file"] = json.loads(marker.read_text())
        seen["status"] = json.loads((tmp_path / "runner-status.json").read_text())
        original()

    sleep.action = lift
    asyncio.run(run())
    assert seen["file"]["rank"] == 9 and seen["file"]["rating"] == 2471
    assert seen["file"]["net"] == "E54 step 50"
    assert seen["status"]["state"] == "paused_top10"
    assert seen["status"]["paused"]["rank"] == 9
    status = json.loads((tmp_path / "runner-status.json").read_text())
    assert status["state"] == "running" and status["paused"] is None


def test_a_pause_survives_a_restart_without_reading_the_rank(tmp_path):
    marker = tmp_path / top_pause.PAUSE_FILE
    marker.write_text(json.dumps({"rank": 8}))
    fetch = _fetcher(40)
    sleep = Sleeper(at=2, action=marker.unlink)
    asyncio.run(top_pause.Watch(tmp_path, "Amy", 10, fetch, sleep).check("x"))
    assert fetch.calls == [] and len(sleep.slept) == 2


def test_a_pause_from_a_wider_line_lifts_itself(tmp_path):
    (tmp_path / "runner-status.json").write_text(json.dumps({"state": "paused_top10"}))
    marker = tmp_path / top_pause.PAUSE_FILE
    marker.write_text(json.dumps({"rank": 10, "rating": 2480}))
    sleep = Sleeper()
    asyncio.run(top_pause.Watch(tmp_path, "Amy", 1, _fetcher(10), sleep).check("x"))
    assert not marker.exists() and sleep.slept == []
    status = json.loads((tmp_path / "runner-status.json").read_text())
    assert status["state"] == "running" and status["paused"] is None


def test_the_line_is_first_place_by_default(monkeypatch):
    monkeypatch.delenv("TOP_PAUSE_RANK", raising=False)
    assert top_pause.threshold() == 1
    monkeypatch.setenv("TOP_PAUSE_RANK", "10")
    assert top_pause.threshold() == 10


def test_an_unreadable_rank_holds_only_near_the_line(tmp_path):
    far = top_pause.Watch(tmp_path, "Amy", 10, _fetcher(0, fail=True), Sleeper())
    asyncio.run(far.check("x"))  # never read: plays on
    assert far.sleep.slept == []

    near = top_pause.Watch(tmp_path, "Amy", 10, _fetcher(0, fail=True), None)
    near.last = 13

    def recover():
        near.fetch = _fetcher(30)

    near.sleep = Sleeper(at=2, action=recover)
    asyncio.run(near.check("x"))
    assert near.sleep.slept == [top_pause.POLL_SECONDS] * 2
    assert not (tmp_path / top_pause.PAUSE_FILE).exists()


def test_off_without_a_name_or_at_zero(tmp_path):
    fetch = _fetcher(1)
    asyncio.run(top_pause.Watch(tmp_path, None, 10, fetch, Sleeper()).check("x"))
    asyncio.run(top_pause.Watch(tmp_path, "Amy", 0, fetch, Sleeper()).check("x"))
    assert fetch.calls == []
