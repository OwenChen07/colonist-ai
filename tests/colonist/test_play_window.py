import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from catanatron.colonist import play_window
from catanatron.colonist.play_window import Window, day_plan, parse

EASTERN = ZoneInfo("America/New_York")


def test_parse_blocks_and_midnight():
    assert parse("18:00-24:00") == [(1080, 1440)]
    assert parse("12:00-15:00, 19:00-22:00") == [(720, 900), (1140, 1320)]
    assert parse("22:00-01:00") == [(1320, 1500)]
    assert parse("") == []


def test_cap_refuses_more_than_six_hours():
    with pytest.raises(ValueError):
        Window.from_env("bot14", {"PLAY_WINDOWS": "12:00-16:00,19:00-22:00"})
    assert Window.from_env("bot13", {"PLAY_WINDOWS": "18:00-24:00"}).on
    assert not Window.from_env("bot13", {}, table="/nonexistent.json").on


def test_table_gives_each_account_its_blocks_within_the_cap(tmp_path):
    table = tmp_path / "w.json"
    table.write_text(json.dumps({"_rule": "x", "bot14": "12:00-15:00"}))
    assert Window.from_env("bot14", {}, table=table).blocks == [(720, 900)]
    assert not Window.from_env("bot13", {}, table=table).on
    # The env line wins over the table.
    env = {"PLAY_WINDOWS": "18:00-20:00"}
    assert Window.from_env("bot14", env, table=table).blocks == [(1080, 1200)]
    # The committed table: every account at six hours or less.
    for account in play_window.read_table():
        assert Window.from_env(account, {}).on


def test_plan_moves_within_jitter_never_grows_and_is_stable():
    blocks = parse("12:00-15:00,19:00-22:00")
    starts = set()
    for n in range(60):
        day = date(2026, 10, 1) + timedelta(days=n)
        plan = day_plan(blocks, "bot14", day, skip_weekly=False)
        assert plan == day_plan(blocks, "bot14", day, skip_weekly=False)
        assert sum((e - s).total_seconds() for s, e in plan) <= 6 * 3600
        midnight = datetime.combine(day, datetime.min.time())
        for (start, end), (s, e) in zip(plan, blocks):
            assert abs((start - midnight).total_seconds() / 60 - s) <= 35
            assert abs((end - midnight).total_seconds() / 60 - e) <= 35
        starts.add(plan[0][0].time())
    assert len(starts) == 60


def test_one_short_block_skipped_a_week():
    blocks = parse("12:00-15:00,19:00-22:00")
    monday = date(2026, 10, 26)
    counts = [
        len(day_plan(blocks, "bot14", monday + timedelta(days=d))) for d in range(7)
    ]
    assert sorted(counts) == [1, 2, 2, 2, 2, 2, 2]
    # A single six-hour block is never skipped.
    six = parse("18:00-24:00")
    assert all(
        len(day_plan(six, "bot13", monday + timedelta(days=d))) == 1 for d in range(7)
    )


def window(blocks, now, **kw):
    return Window(parse(blocks), "bot16", skip_weekly=False, clock=lambda: now, **kw)


def test_open_and_closed_across_midnight():
    noon = datetime(2026, 10, 27, 12, 0, tzinfo=EASTERN)
    w = window("22:00-01:00", noon)
    assert w.current() is None
    assert w.next_open().date() == date(2026, 10, 27)
    late = datetime(2026, 10, 28, 0, 15, tzinfo=EASTERN)
    assert window("22:00-01:00", late, jitter=0).current() is not None
    assert window("", noon).current()


def test_wait_open_writes_status_and_waits(tmp_path):
    times = [datetime(2026, 10, 27, 12, 0, tzinfo=EASTERN)]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        times[0] += timedelta(seconds=seconds)

    w = Window(
        parse("18:00-24:00"),
        "bot13",
        skip_weekly=False,
        sleep=sleep,
        clock=lambda: times[0],
    )
    asyncio.run(w.wait_open(tmp_path))
    status = json.loads((tmp_path / "runner-status.json").read_text())
    assert status["state"] == play_window.STATE
    assert w.current() is not None
    assert max(slept) <= play_window.MAX_SLEEP
    assert 5 * 3600 < sum(slept) < 7 * 3600
