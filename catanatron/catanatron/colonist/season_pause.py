"""Pause at a high near the season's end (live-play's draft, Oct 22; off unless
SEASON_HIGH_PAUSE=1 in the account's env file, the lead's call).

The season's end comes from colonist's leaderboard API
(/api/leaderboards-tabs/global, activeSeasonData.endDate), the date the
leaderboard page counts down to; SEASON_END (ISO time) stands in only when the
API can't be read. Before each queue the runner reads the account's global 1v1
rating and rank and appends them to HISTORY in its data folder. Then:

- last 24 hours: our highest-rated account (the leader among PEERS) and any
  account in the global top ten pause when the rating is at or above the best
  rating in HISTORY over the last 24 hours;
- last 3 hours: our three highest-rated accounts pause whatever their rating.

A pause writes PAUSE_FILE in the account's data folder and holds there, like
the top-ten pause: it survives restarts and VM moves, and only the owner or
the lead lifts it, by deleting the file. A game in progress always finishes:
the check runs between games only. PEERS are the names in SEASON_PEERS, or
else every name in COLONIST_BOT_NAMES. A rating that can't be read plays on.
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

from catanatron.colonist import top_pause
from catanatron.colonist.own_queue import update_status

PAUSE_FILE = "paused-high.json"
HISTORY = "rating-history.jsonl"
STATE = "paused_high"
HIGH_WINDOW = 24 * 3600  # the last day: pause at a 24-h high
TOP_WINDOW = 3 * 3600  # the last 3 hours: the top three pause outright
TOP_COUNT = 3
TOP_RANK = 10
END_REFRESH = 6 * 3600  # read the season's end again after this long


def enabled():
    return os.environ.get("SEASON_HIGH_PAUSE", "") == "1"


def _epoch(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_end(fetch=top_pause._fetch):
    """The current season's end (epoch seconds) from the leaderboard API."""
    season = fetch("leaderboards-tabs/global").get("activeSeasonData") or {}
    return _epoch(season.get("endDate"))


def best_since(data, since):
    """The highest rating in HISTORY at or after since, or None."""
    best = None
    path = Path(data) / HISTORY
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rating = row.get("rating")
        if isinstance(rating, int) and row.get("t", 0) >= since:
            best = rating if best is None else max(best, rating)
    return best


class Watch:
    """The runner's between-games season check, after the top-ten one."""

    def __init__(
        self,
        data,
        name,
        peers=(),
        fetch=top_pause._fetch,
        sleep=None,
        clock=time.time,
        on=None,
    ):
        self.data, self.name = Path(data), name
        self.peers = [p for p in dict.fromkeys(peers) if p]
        if name and name not in self.peers:
            self.peers.append(name)
        self.fetch, self.clock = fetch, clock
        self.sleep = sleep or asyncio.sleep
        self.on = enabled() if on is None else on
        self.end, self.end_read = None, None
        self.last_place = None  # our place among peers, last read

    def season_end(self):
        now = self.clock()
        if self.end_read is None or now - self.end_read >= END_REFRESH:
            try:
                end = read_end(self.fetch)
            except Exception as error:  # noqa: BLE001 - colonist unreachable
                print(f"season pause: end unreadable ({error!s:.60})", flush=True)
                end = None
            if end is not None:
                self.end, self.end_read = end, now
            elif self.end is None:
                self.end = _epoch(os.environ.get("SEASON_END", ""))
        return self.end

    def _rating(self, name):
        try:
            return top_pause.read_rank(name, self.fetch)
        except Exception as error:  # noqa: BLE001 - colonist unreachable
            print(f"season pause: {name} unreadable ({error!s:.60})", flush=True)
            return None, None

    def place(self, rating):
        """Our place by rating among PEERS (1 = the leader); the last one read
        when this rating is unknown."""
        if rating is None:
            return self.last_place
        others = [self._rating(p)[1] for p in self.peers if p != self.name]
        self.last_place = 1 + sum(1 for r in others if r is not None and r > rating)
        return self.last_place

    def decide(self):
        """The clause that pauses the account now, as a dict, or None."""
        end = self.season_end()
        now = self.clock()
        if end is None:
            return None
        left = end - now
        if left <= 0 or left > HIGH_WINDOW:
            return None
        rank, rating = self._rating(self.name)
        best = best_since(self.data, now - HIGH_WINDOW)  # before this reading
        if rating is not None:
            with open(self.data / HISTORY, "a") as f:
                f.write(json.dumps({"t": int(now), "rating": rating, "rank": rank}))
                f.write("\n")
        place = self.place(rating)
        found = {"rank": rank, "rating": rating, "place": place, "end": int(end)}
        if left <= TOP_WINDOW and place is not None and place <= TOP_COUNT:
            return {**found, "rule": f"last 3 hours, our top {TOP_COUNT}"}
        if rating is None:
            return None
        if place == 1 or (rank is not None and rank <= TOP_RANK):
            if best is None or rating >= best:
                return {**found, "best_24h": best, "rule": "last 24 hours, a high"}
        return None

    async def check(self, label):
        """Before a queue: hold here while the account is paused."""
        if not self.on or not self.name:
            return
        path = self.data / PAUSE_FILE
        if path.exists():
            print(f"season pause: held by {PAUSE_FILE}", flush=True)
            try:
                found = json.loads(path.read_text())
            except (OSError, ValueError):
                found = {}
        else:
            hit = self.decide()
            if hit is None:
                return
            found = {**hit, "name": self.name, "net": label, "at": int(self.clock())}
            path.write_text(json.dumps(found, indent=2))
            print(
                f"season pause: {found['rule']} at {found['rating']} "
                f"(#{found['rank']}); no new game until {PAUSE_FILE} is removed",
                flush=True,
            )
        update_status(
            self.data,
            state=STATE,
            paused=found,
            queue_state="other",
            at=int(time.time()),
        )
        while path.exists():
            await self.sleep(top_pause.POLL_SECONDS)
        print("season pause lifted: queueing again", flush=True)
        update_status(self.data, state="running", paused=None, at=int(time.time()))
