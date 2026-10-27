"""Daily play windows: an account plays at most six hours a day, at hours a
person in its region would play (owner's order, Oct 27, 8:44 AM Toronto, after
bot10's ban).

scripts/vm/bot/play_windows.json lists each account's blocks (PLAY_WINDOWS in
its env file overrides it), local time in PLAY_TZ (default US Eastern), e.g.
"18:00-24:00" or "12:00-15:00,19:00-22:00"; a block may run past midnight
("22:00-01:00"). An account with neither plays at any hour, as before. Blocks over PLAY_HOURS_MAX (6) hours a day in total are
refused.

Each day every block moves by up to PLAY_JITTER minutes (default 30) either
way, and each of its edges comes in by up to EDGE_TRIM minutes more, so no two
days start at the same minute and a block never grows. Once a week one block of
three hours or less is skipped (PLAY_SKIP_WEEKLY=0 turns that off). The draws
are seeded by account and date, so a restart keeps the same day's plan.

The runner checks between games only: a game in progress always finishes, then
the runner stops queueing and exits; systemd starts it again and it waits here,
browser and bridge closed (offline), until the next block opens.
"""

import asyncio
import json
import os
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = "off_hours"
EDGE_TRIM = 5.0  # minutes: each edge comes in by up to this much
SHORT_BLOCK = 180  # minutes: blocks this short can be the weekly skip
MAX_SLEEP = 600  # seconds: wake to look at the clock again at least this often
# Each account's blocks, synced to the bot VMs with the code (sync_bot.sh).
TABLE = Path(__file__).resolve().parents[3] / "scripts/vm/bot/play_windows.json"


def read_table(path=TABLE):
    """{account: PLAY_WINDOWS text}; empty when the file is missing."""
    try:
        doc = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def parse(text):
    """[(start, end)] in minutes after midnight; end may pass 1440."""
    blocks = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        start, end = (_minutes(x) for x in part.split("-"))
        if end <= start:
            end += 1440
        if end - start > 1440:
            raise ValueError(f"play window longer than a day: {part}")
        blocks.append((start, end))
    return blocks


def _minutes(text):
    hours, _, minutes = text.strip().partition(":")
    value = int(hours) * 60 + int(minutes or 0)
    if not 0 <= value <= 1440:
        raise ValueError(f"not a time of day: {text}")
    return value


def skipped_block(blocks, account, day):
    """The block index skipped on this day (the week's one skip), or None."""
    short = [i for i, (s, e) in enumerate(blocks) if e - s <= SHORT_BLOCK]
    if not short:
        return None
    year, week, weekday = day.isocalendar()
    rng = random.Random(f"{account}:skip:{year}-{week}")
    skip_day, skip = rng.randrange(1, 8), rng.choice(short)
    return skip if weekday == skip_day else None


def day_plan(blocks, account, day, jitter=30.0, skip_weekly=True):
    """[(start, end)] naive local datetimes of the blocks that start on day."""
    midnight = datetime.combine(day, datetime.min.time())
    skip = skipped_block(blocks, account, day) if skip_weekly else None
    plan = []
    for i, (start, end) in enumerate(blocks):
        if i == skip:
            continue
        rng = random.Random(f"{account}:{day.isoformat()}:{i}")
        shift = rng.uniform(-jitter, jitter)
        start += shift + rng.uniform(0, EDGE_TRIM)
        end += shift - rng.uniform(0, EDGE_TRIM)
        plan.append(
            (midnight + timedelta(minutes=start), midnight + timedelta(minutes=end))
        )
    return plan


class Window:
    def __init__(
        self,
        blocks,
        account,
        tz="America/New_York",
        jitter=30.0,
        skip_weekly=True,
        sleep=asyncio.sleep,
        clock=None,
    ):
        self.blocks, self.account = blocks, account or "ranked"
        self.tz = ZoneInfo(tz)
        self.jitter, self.skip_weekly = jitter, skip_weekly
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(self.tz))

    @classmethod
    def from_env(cls, account, environ=None, table=TABLE):
        env = os.environ if environ is None else environ
        text = env.get("PLAY_WINDOWS")
        if text is None:
            text = read_table(table).get(account or "ranked", "")
        blocks = parse(text)
        cap = float(env.get("PLAY_HOURS_MAX") or 6)
        hours = sum(e - s for s, e in blocks) / 60
        if hours > cap:
            raise ValueError(
                f"PLAY_WINDOWS gives {hours:g} h a day; the cap is {cap:g} h"
            )
        return cls(
            blocks,
            account,
            tz=env.get("PLAY_TZ") or "America/New_York",
            jitter=float(env.get("PLAY_JITTER") or 30),
            skip_weekly=env.get("PLAY_SKIP_WEEKLY", "1") != "0",
        )

    @property
    def on(self):
        return bool(self.blocks)

    def _now(self, now=None):
        now = now or self.clock()
        return now.astimezone(self.tz).replace(tzinfo=None)

    def _plans(self, today, days):
        for offset in days:
            day = today + timedelta(days=offset)
            yield from day_plan(
                self.blocks, self.account, day, self.jitter, self.skip_weekly
            )

    def current(self, now=None):
        """The block open now as (start, end), or None. Always open when off."""
        if not self.on:
            return True
        now = self._now(now)
        for start, end in self._plans(now.date(), (-1, 0)):
            if start <= now < end:
                return start, end
        return None

    def next_open(self, now=None):
        """The next block's start, naive local time; None when off."""
        if not self.on:
            return None
        now = self._now(now)
        starts = [s for s, _ in self._plans(now.date(), range(-1, 9)) if s > now]
        return min(starts) if starts else None

    async def wait_open(self, data, label=None):
        """At start: hold here, writing runner-status, until a block opens."""
        if not self.on or self.current():
            return
        nxt = self.next_open()
        print(
            f"play window: closed; next opens about {nxt:%a %b %d %H:%M} "
            f"{self.tz.key}",
            flush=True,
        )
        (Path(data) / "runner-status.json").write_text(
            json.dumps(
                {
                    "state": STATE,
                    "pid": os.getpid(),
                    "label": label,
                    "next_window": f"{nxt:%Y-%m-%d %H:%M}",
                    "tz": self.tz.key,
                    "at": int(time.time()),
                },
                indent=2,
            )
        )
        while not self.current():
            nxt = self.next_open()
            left = (nxt - self._now()).total_seconds() if nxt else MAX_SLEEP
            await self.sleep(min(MAX_SLEEP, max(30, left)))
        start, end = self.current()
        print(f"play window: open until about {end:%H:%M}", flush=True)
