"""Keep our own accounts from meeting each other in ranked (E62).

Two of our accounts that queue at the same time at similar ratings are often
matched together (about 6 games a day in Oct 14-19). Before a ranked queue the
runner waits while another of our accounts is in the ranked queue, until that
one is in a game. Step 3 (Oct 27): the wait no longer gives up after 90 s; an
account seen queued for STALE_QUEUED seconds is taken as dead (a stuck runner
must not block everyone), and MAX_WAIT is only a last resort. This is
pre-queue only: a matched game is always played. Every wait is appended to
own-queue.jsonl in the account's folder.

The signal: each runner records in runner-status.json whether it is waiting to
queue ("waiting") or in the queue ("queued"). Accounts on this VM are read from
their folders directly; accounts on the other VM from the spectator site's
/api/live, where spectator_publish shows a waiting account with queue null and
a queued one as searching with queue "ranked". Two waiting accounts resolve
the tie by name: the earlier name queues first.
"""

import asyncio
import json
import random
import time
import urllib.request
from pathlib import Path

from catanatron.colonist.spectator_publish import live_game

SITE = "https://colonist-ai-spectator.vercel.app"
MAX_WAIT = 600
STALE_QUEUED = 180
POLL_SECONDS = 12
LOG = "own-queue.jsonl"
JITTER_SECONDS = 20


def site_setting(value):
    """AVOID_OWN_SITE: unset is the spectator site (on for every account,
    E62 step 2); empty, 0 or off turns the wait off; anything else is a URL."""
    if value is None:
        return SITE
    if value.strip().lower() in ("", "0", "off"):
        return None
    return value.strip()


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _runner_alive(pid):
    """A live ranked_runner: a copied folder's pid may be another process."""
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, TypeError, ValueError):
        return False
    return b"ranked_runner" in command


def update_status(data, **fields):
    """Add fields to the runner's runner-status.json, keeping the rest."""
    path = Path(data) / "runner-status.json"
    doc = _read_json(path) or {}
    doc.update(fields)
    path.write_text(json.dumps(doc, indent=2))


def local_states(data, names, alive=_runner_alive):
    """{colonist name: "waiting" | "queued" | "other"} for this VM's accounts.
    data is this account's folder; its siblings are the other accounts."""
    states = {}
    now = time.time()
    for account, name in names.items():
        folder = Path(data).parent / account
        runner = _read_json(folder / "runner-status.json")
        if not runner or not alive(runner.get("pid")):
            continue
        if runner.get("state") not in ("starting", "running"):
            continue
        if live_game(folder, now):
            states[name] = "other"
        else:
            states[name] = runner.get("queue_state") or "other"
    return states


def _freshness(bot):
    """A name can appear under two sources (a VM that posted under a second
    hostname): an entry that isn't offline wins, then the newest updatedAt."""
    return (bot.get("status") != "offline", str(bot.get("updatedAt") or ""))


def site_states(bots):
    """The same from /api/live's bots: searching in ranked = queued, searching
    with no queue = waiting to queue; casual accounts don't count."""
    newest = {}
    for bot in bots or []:
        if not isinstance(bot, dict) or not isinstance(bot.get("name"), str):
            continue
        seen = newest.get(bot["name"])
        if seen is None or _freshness(bot) > _freshness(seen):
            newest[bot["name"]] = bot
    states = {}
    for bot in newest.values():
        if bot.get("status") != "searching":
            states[bot["name"]] = "other"
        elif bot.get("queue") == "ranked":
            states[bot["name"]] = "queued"
        elif bot.get("queue") is None:
            states[bot["name"]] = "waiting"
        else:
            states[bot["name"]] = "other"
    return states


def blockers(states, me, own, dead=()):
    """Our accounts this one should wait for: any in the ranked queue (unless
    taken as dead), and any waiting to queue whose name comes first."""
    return sorted(
        name
        for name, state in states.items()
        if name != me
        and name in own
        and (
            (state == "queued" and name not in dead)
            or (state == "waiting" and name < me)
        )
    )


def fetch_site(site, timeout=8):
    with urllib.request.urlopen(
        site.rstrip("/") + "/api/live", timeout=timeout
    ) as response:
        return json.loads(response.read()).get("bots") or []


def current_states(site, data, names, fetch=fetch_site, alive=_runner_alive):
    """The site's view, overridden by this VM's own folders (exact, no lag).
    A site that can't be read counts as no one queued."""
    try:
        states = site_states(fetch(site))
    except Exception as error:  # noqa: BLE001 - never stop a runner for this
        print(f"own-queue check: site unreadable ({error!s:.60})", flush=True)
        states = {}
    states.update(local_states(data, names, alive))
    return states


async def wait_for_own_accounts(
    site,
    data,
    me,
    own,
    names,
    fetch=fetch_site,
    alive=_runner_alive,
    sleep=asyncio.sleep,
    clock=time.monotonic,
    jitter=None,
    max_wait=MAX_WAIT,
    poll=POLL_SECONDS,
    stale=STALE_QUEUED,
):
    """Wait before a ranked queue while another of our accounts is queued.
    An account seen queued for `stale` seconds of this wait is ignored (the
    site has no queue time, so its age counts from when this wait first saw
    it). Returns the seconds waited (jitter included)."""
    start = clock()
    update_status(data, queue_state="waiting", waiting_since=int(time.time()))
    await sleep(random.uniform(0, JITTER_SECONDS) if jitter is None else jitter)
    seen = set()
    queued_since = {}
    dead = set()
    outcome = "clear"
    while True:
        states = current_states(site, data, names, fetch, alive)
        now = clock()
        queued_since = {
            name: queued_since.get(name, now)
            for name, state in states.items()
            if state == "queued"
        }
        dead = {name for name, since in queued_since.items() if now - since >= stale}
        waiting = blockers(states, me, own, dead)
        if not waiting:
            break
        seen.update(waiting)
        if now - start >= max_wait:
            outcome = "cap"
            break
        await sleep(poll)
    waited = clock() - start
    dead = sorted(dead & set(own) - {me})
    if outcome == "clear" and dead:
        outcome = "stale"
    if seen or dead:
        print(
            f"own-queue check: waited {waited:.0f} s for {len(seen)} of our accounts"
            f" ({outcome}; {len(dead)} queued over {stale} s ignored)",
            flush=True,
        )
    row = {
        "at": int(time.time()),
        "waited": round(waited, 1),
        "blockers": len(seen),
        "outcome": outcome,
        "stale": len(dead),
    }
    try:
        with (Path(data) / LOG).open("a") as log:
            log.write(json.dumps(row) + "\n")
    except OSError:
        pass
    return waited


def mark_queued(data):
    update_status(data, queue_state="queued", queued_at=int(time.time()))
