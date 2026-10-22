"""Pause an account that reaches the global top ten (owner's rule, Oct 22,
10:48 AM Toronto, standing, all accounts).

Before each queue the runner reads the account's global 1v1 rank from its
public colonist profile: /api/profile/NAME/ranked/SEASON, the 1v1 mode
(modeType 1), rankedStanding's first row (the global rank), the same field the
spectator site and status.py show as "#N". At rank 10 or better it writes
PAUSE_FILE in the account's data folder and holds: the browser and bridge stay
up, no game is queued. The file keeps the pause through a restart or a VM move
(it travels with the folder, and the rank is read again anyway); only the
owner lifts it, by deleting the file. A game in progress always finishes: the
check runs between games only.

A rank that can't be read lets the game queue, unless the last rank read was
within NEAR of the line; then the runner waits and reads again.
"""

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from catanatron.colonist.own_queue import update_status

API = "https://colonist.io/api"
HEADERS = {"User-Agent": "colonist-ai-status/1.0", "Accept": "application/json"}
PAUSE_FILE = "paused-top10.json"
STATE = "paused_top10"
NEAR = 5  # ranks above the line at which an unreadable rank holds the queue
SETTLE_SECONDS = 20  # close to the line: read again once the last game counts
POLL_SECONDS = 60


def threshold():
    """TOP_PAUSE_RANK (default 10); 0 turns the pause off."""
    try:
        return int(os.environ.get("TOP_PAUSE_RANK", "10"))
    except ValueError:
        return 10


def _fetch(path, timeout=15):
    request = urllib.request.Request(f"{API}/{path}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _number(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_standing(modes):
    """(global rank, rating) from a profile's ranked modes; None when absent."""
    duel = next(
        (m for m in modes or [] if isinstance(m, dict) and m.get("modeType") == 1),
        None,
    )
    if not duel:
        return None, None
    standing = duel.get("rankedStanding") or [{}]
    return _number(standing[0].get("rank")), _number(duel.get("skillRating"))


def read_rank(name, fetch=_fetch):
    seasons = fetch("profile/ranked-seasons")
    season = max(
        s["seasonType"] for s in seasons if isinstance(s.get("seasonType"), int)
    )
    return parse_standing(fetch(f"profile/{urllib.parse.quote(name)}/ranked/{season}"))


def paused(data):
    path = Path(data) / PAUSE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


class Watch:
    """The runner's between-games check. last is the last rank read."""

    def __init__(self, data, name, line=None, fetch=_fetch, sleep=asyncio.sleep):
        self.data, self.name = Path(data), name
        self.line = threshold() if line is None else line
        self.fetch, self.sleep = fetch, sleep
        self.last = None

    def _read(self):
        try:
            rank, rating = read_rank(self.name, self.fetch)
        except Exception as error:  # noqa: BLE001 - colonist unreachable
            print(f"top-ten check: rank unreadable ({error!s:.60})", flush=True)
            return None, None, False
        if rank is not None:
            self.last = rank
        return rank, rating, True

    async def should_pause(self):
        """(rank, rating) when the account must pause now, else None."""
        while True:
            rank, rating, ok = self._read()
            if ok:
                break
            if self.last is None or self.last > self.line + NEAR:
                return None
            await self.sleep(POLL_SECONDS)
        if rank is not None and self.line < rank <= self.line + NEAR:
            # Near the line: give colonist time to count the last game.
            await self.sleep(SETTLE_SECONDS)
            rank, rating, ok = self._read()
        if rank is not None and rank <= self.line:
            return rank, rating
        return None

    async def check(self, label):
        """Before a queue: hold here while the account is paused."""
        if not self.name or self.line <= 0:
            return
        found = paused(self.data)
        if found is None:
            hit = await self.should_pause()
            if hit is None:
                return
            found = {
                "rank": hit[0],
                "rating": hit[1],
                "name": self.name,
                "net": label,
                "at": int(time.time()),
                "rule": f"global top {self.line}: paused until the owner says",
            }
            (self.data / PAUSE_FILE).write_text(json.dumps(found, indent=2))
            print(
                f"top-ten pause: global #{hit[0]} at {hit[1]}; no new game "
                f"until {PAUSE_FILE} is removed",
                flush=True,
            )
        else:
            print(f"top-ten pause: held by {PAUSE_FILE}", flush=True)
        update_status(
            self.data,
            state=STATE,
            paused=found,
            queue_state="other",
            at=int(time.time()),
        )
        while paused(self.data) is not None:
            await self.sleep(POLL_SECONDS)
        print("top-ten pause lifted: queueing again", flush=True)
        update_status(self.data, state="running", paused=None, at=int(time.time()))
