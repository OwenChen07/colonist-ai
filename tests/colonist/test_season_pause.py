import asyncio
import json

from catanatron.colonist import season_pause

END = 1_793_491_200  # Nov 1, 00:00 UTC: season 18's end, Oct 31, 8 PM Toronto
HOUR = 3600


def _fetcher(ratings, ranks=None, end="2026-11-01T00:00:00.000Z", fail=()):
    """ratings: {name: rating}; ranks: {name: global rank} (default 50)."""
    calls = []

    def fetch(path):
        calls.append(path)
        if path in fail or any(path.startswith(f"profile/{n}/") for n in fail):
            raise OSError("down")
        if path == "leaderboards-tabs/global":
            return {"activeSeasonData": {"rankedSeasonType": 21, "endDate": end}}
        if path == "profile/ranked-seasons":
            return [{"seasonType": 21}]
        name = path.split("/")[1]
        rank = (ranks or {}).get(name, 50)
        return [
            {
                "modeType": 1,
                "skillRating": str(ratings[name]),
                "rankedStanding": [{"rank": str(rank)}],
            }
        ]

    fetch.calls = calls
    return fetch


class Sleeper:
    def __init__(self, at=None, action=None):
        self.slept, self.at, self.action = [], at, action

    async def __call__(self, seconds):
        self.slept.append(seconds)
        if self.action and len(self.slept) == self.at:
            self.action()


def _watch(tmp_path, ratings, left, name="Amy", ranks=None, sleep=None, **kw):
    return season_pause.Watch(
        tmp_path,
        name,
        list(ratings),
        _fetcher(ratings, ranks, **kw),
        sleep or Sleeper(),
        clock=lambda: END - left,
        on=True,
    )


def _history(tmp_path, *rows):
    with open(tmp_path / season_pause.HISTORY, "w") as f:
        for t, rating in rows:
            f.write(json.dumps({"t": t, "rating": rating, "rank": 30}) + "\n")


def test_reads_the_season_end_from_the_leaderboard():
    assert season_pause.read_end(_fetcher({})) == END


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SEASON_HIGH_PAUSE", raising=False)
    fetch = _fetcher({"Amy": 2500})
    watch = season_pause.Watch(tmp_path, "Amy", ["Amy"], fetch, Sleeper())
    asyncio.run(watch.check("x"))
    assert fetch.calls == [] and not (tmp_path / season_pause.PAUSE_FILE).exists()
    monkeypatch.setenv("SEASON_HIGH_PAUSE", "1")
    assert season_pause.Watch(tmp_path, "Amy").on


def test_more_than_a_day_left_plays_on_and_keeps_no_history(tmp_path):
    watch = _watch(tmp_path, {"Amy": 2500, "Bo": 2300}, 30 * HOUR)
    assert watch.decide() is None
    assert not (tmp_path / season_pause.HISTORY).exists()


def test_the_leader_pauses_at_a_24h_high(tmp_path):
    now = END - 10 * HOUR
    _history(tmp_path, (now - 30 * HOUR, 2600), (now - 5 * HOUR, 2480))
    hit = _watch(tmp_path, {"Amy": 2490, "Bo": 2300}, 10 * HOUR).decide()
    assert hit["rule"] == "last 24 hours, a high" and hit["best_24h"] == 2480
    assert hit["place"] == 1 and hit["rating"] == 2490
    rows = (tmp_path / season_pause.HISTORY).read_text().splitlines()
    assert json.loads(rows[-1])["rating"] == 2490


def test_the_leader_below_its_24h_best_plays_on(tmp_path):
    _history(tmp_path, (END - 12 * HOUR, 2520))
    assert _watch(tmp_path, {"Amy": 2490, "Bo": 2300}, 10 * HOUR).decide() is None


def test_a_second_account_pauses_only_in_the_top_ten(tmp_path):
    ratings = {"Amy": 2400, "Bo": 2450}
    assert _watch(tmp_path, ratings, 10 * HOUR).decide() is None
    hit = _watch(tmp_path, ratings, 10 * HOUR, ranks={"Amy": 9}).decide()
    assert hit["rule"] == "last 24 hours, a high" and hit["place"] == 2


def test_the_top_three_pause_in_the_last_three_hours(tmp_path):
    _history(tmp_path, (END - 4 * HOUR, 2600))
    ratings = {"Amy": 2300, "Bo": 2450, "Cy": 2400, "Di": 2100}
    hit = _watch(tmp_path, ratings, 2 * HOUR).decide()
    assert hit["rule"] == "last 3 hours, our top 3" and hit["place"] == 3
    assert _watch(tmp_path, ratings, 2 * HOUR, name="Di").decide() is None


def test_after_the_end_plays_on(tmp_path):
    assert _watch(tmp_path, {"Amy": 2500}, -60).decide() is None


def test_an_unreadable_end_uses_season_end_or_plays_on(tmp_path, monkeypatch):
    fail = ("leaderboards-tabs/global",)
    monkeypatch.delenv("SEASON_END", raising=False)
    assert _watch(tmp_path, {"Amy": 2500}, HOUR, fail=fail).decide() is None
    monkeypatch.setenv("SEASON_END", "2026-11-01T00:00:00Z")
    hit = _watch(tmp_path, {"Amy": 2500}, HOUR, fail=fail).decide()
    assert hit["end"] == END


def test_an_unreadable_rating_plays_on_unless_last_seen_in_the_top_three(tmp_path):
    watch = _watch(tmp_path, {"Amy": 2500, "Bo": 2300}, 2 * HOUR, fail=("Amy",))
    assert watch.decide() is None
    watch.last_place = 2
    assert watch.decide()["rule"] == "last 3 hours, our top 3"


def test_a_pause_holds_until_the_file_is_removed_and_survives_restarts(tmp_path):
    (tmp_path / "runner-status.json").write_text(json.dumps({"state": "running"}))
    marker = tmp_path / season_pause.PAUSE_FILE
    seen = {}

    def lift():
        seen["file"] = json.loads(marker.read_text())
        seen["status"] = json.loads((tmp_path / "runner-status.json").read_text())
        marker.unlink()

    sleep = Sleeper(at=2, action=lift)
    watch = _watch(tmp_path, {"Amy": 2500}, 2 * HOUR, sleep=sleep)
    asyncio.run(watch.check("E54 step 50"))
    assert seen["file"]["net"] == "E54 step 50" and seen["file"]["rating"] == 2500
    assert seen["status"]["state"] == season_pause.STATE
    status = json.loads((tmp_path / "runner-status.json").read_text())
    assert status["state"] == "running" and status["paused"] is None

    marker.write_text(json.dumps({"rating": 2500}))
    fetch_watch = _watch(tmp_path, {"Amy": 2000}, 30 * HOUR)
    fetch_watch.sleep = Sleeper(at=1, action=marker.unlink)
    asyncio.run(fetch_watch.check("x"))
    assert fetch_watch.fetch.calls == []
