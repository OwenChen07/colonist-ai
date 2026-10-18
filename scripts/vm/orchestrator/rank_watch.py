"""Screenshots any bot account the moment it is #1 Global in 1v1 ranked,
or when it reaches the highest 1v1 rating any of the bots has had.

colonist's public profile lists each account's 1v1 standing globally, on its
continent, in its country and in its region (e.g. Ontario); only the global
one counts (owner, Oct 18). When it is 1, this saves a screenshot of the
profile's Ranked tab at once (the rank may not last) and queues it; the
Discord bot runs this every 10 minutes and posts what's queued in daytime
(--release) to #milestones. The rating record across the accounts is kept in
rank-watch.json ("record").

    .venv/bin/python scripts/vm/orchestrator/rank_watch.py [--release]

Prints one JSON line: {"posts": [{"text": ..., "image": ...}]} (only with
--release; otherwise the list is empty and the queue keeps them).
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402
from context import SITE, STATE, read_json  # noqa: E402

API = "https://colonist.io/api"
HEADERS = {"User-Agent": "colonist-ai-status/1.0", "Accept": "application/json"}
SHOTS = STATE / "screenshots"
DEFAULT_ACCOUNTS = "Petra4620,Dilly6283,Sherie3424,Dale7112"


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def accounts():
    """The bots the site shows (the active ones), else the env list."""
    try:
        names = [b["name"] for b in fetch(SITE + "/api/live").get("bots", [])]
        if names:
            return names
    except Exception:  # the site is optional
        pass
    raw = os.environ.get("ORCHESTRATOR_RANK_ACCOUNTS", DEFAULT_ACCOUNTS)
    return [n for n in raw.split(",") if n]


def level_name(entry):
    """'Global', 'North America', 'CA', 'Ontario' … from a rankedStanding row."""
    name = entry.get("name")
    if isinstance(name, dict):
        key = name.get("key", "")
        if key.endswith(".global"):
            return "Global"
        return {"NA": "North America", "EU": "Europe", "AS": "Asia"}.get(
            key.rsplit(".", 1)[-1], key.rsplit(".", 1)[-1]
        )
    return str(name)


def firsts(standing):
    """The levels at which a rankedStanding list says #1: Global or nothing
    (continent, country and region #1s don't count)."""
    return [
        level_name(e)
        for e in standing or []
        if str(e.get("rank")) == "1" and level_name(e) == "Global"
    ]


def standing(name, season):
    modes = fetch(f"{API}/profile/{name}/ranked/{season}")
    duel = next((m for m in modes if m.get("modeType") == 1), None)
    return duel or {}


def screenshot(name, path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            # colonist keeps sockets open, so wait for load plus a pause.
            page.goto(f"https://colonist.io/profile/{name}#ranked", timeout=60000)
            page.wait_for_timeout(5000)
            page.get_by_text("Ranked", exact=True).first.click()
            page.wait_for_timeout(6000)
            page.screenshot(path=str(path))
        finally:
            browser.close()


def stamp(when):
    return f"{core.local(when):%b %d %-I:%M %p}"


def global_rank(standing):
    return next(
        (e.get("rank") for e in standing or [] if level_name(e) == "Global"), None
    )


def new_high(state, duels, now):
    """Shoots and queues the best bot's rating when it beats the record.

    The record is the highest 1v1 rating any bot has had; the first run seeds
    it silently from the current best.
    """
    rated = [
        (d["skillRating"], name)
        for name, d in duels.items()
        if isinstance(d.get("skillRating"), (int, float))
    ]
    if not rated:
        return
    rating, name = max(rated)
    record = state.get("record")
    if record is None:
        state["record"] = {"rating": rating, "name": name, "at": now.isoformat()}
        return
    if rating <= record["rating"]:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}-high-{rating}-{now:%Y%m%d-%H%M}.png"
    try:
        screenshot(name, path)
    except Exception as error:
        print(f"screenshot failed for {name}: {error}", file=sys.stderr)
        return  # the record stays, so the next run tries again
    previous = dt.datetime.fromisoformat(record["at"])
    rank = global_rank(duels[name].get("rankedStanding"))
    state["queue"].append(
        {
            "text": f"📈 **New high: {name} at {rating}** (previous record "
            f"{record['rating']} by {record['name']}, {stamp(previous)})"
            + (f", global #{rank}" if rank else "")
            + f", at {stamp(now)} Toronto",
            "image": str(path),
        }
    )
    state["record"] = {"rating": rating, "name": name, "at": now.isoformat()}


def check(state, now):
    seasons = fetch(f"{API}/profile/ranked-seasons")
    season = max(
        s["seasonType"] for s in seasons if isinstance(s.get("seasonType"), int)
    )
    duels = {}
    for name in accounts():
        try:
            duel = standing(name, season)
        except Exception:
            continue
        duels[name] = duel
        for level in firsts(duel.get("rankedStanding")):
            key = f"{season}:{name}:{level}"
            if key in state["seen"]:
                continue
            SHOTS.mkdir(parents=True, exist_ok=True)
            path = SHOTS / f"{name}-{level.replace(' ', '_')}-{now:%Y%m%d-%H%M}.png"
            try:
                screenshot(name, path)
            except Exception as error:
                print(f"screenshot failed for {name}: {error}", file=sys.stderr)
                continue  # try again next run
            state["seen"].append(key)
            state["queue"].append(
                {
                    "text": f"🏆 **{name} is #1 in {level}** (1v1 ranked, "
                    f"{duel.get('skillRating')} rating), at "
                    f"{stamp(now)} Toronto",
                    "image": str(path),
                }
            )
    new_high(state, duels, now)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="hand over the queue")
    args = parser.parse_args(argv)
    path = STATE / "rank-watch.json"
    state = read_json(path) or {"seen": [], "queue": []}
    now = dt.datetime.now(dt.timezone.utc)
    try:
        check(state, now)
    except Exception as error:  # colonist unreachable: try next time
        print(f"rank check failed: {error}", file=sys.stderr)
    posts = state["queue"] if args.release else []
    if args.release:
        state["queue"] = []
    path.write_text(json.dumps(state, indent=1))
    print(json.dumps({"posts": posts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
