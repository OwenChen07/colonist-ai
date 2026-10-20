"""Posts our accounts' ranked 1v1 milestones the moment they happen (owner,
Oct 20, 9:02 AM: at any hour, never a ping).

Milestones, each posted once with a screenshot of the profile's Ranked tab:
- per account, the rating crossing 2450, 2500 … 2650 (upward only) and the
  global rank entering the top 25, 10, 5, 3 and #1 (improvements only);
- across our accounts, a new all-time best rating ("record") and a new best
  global rank ("best_rank"), seeded at Sherie3424's 2460 and #16.
colonist's profile also lists continent, country and region ranks; only the
global one counts (owner, Oct 18). The per-account keys in rank-watch.json
("seen") carry the season, so a new season starts them again and a rating
that dips and recrosses a line posts once. The first run marks what already
stands without posting. The Discord bot runs this every 5 minutes.

    .venv/bin/python scripts/vm/orchestrator/rank_watch.py [--release] [--dry-run]

Prints one JSON line: {"posts": [{"text": ..., "image": ...}]} (with
--release, else the queue keeps them; --dry-run prints them, takes no
screenshot and saves nothing).
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
DEFAULT_ACCOUNTS = "Petra4620,Dilly6283,Sherie3424,Dale7112,Vasos5306"
RATING_LINES = (2450, 2500, 2550, 2600, 2650)  # per account, upward only
RANK_TIERS = (25, 10, 5, 3, 1)  # global top N, per account, improvements only
# Our bests before this watch tracked them (lead, Oct 20 8 AM report).
SEED_RECORD = {"rating": 2460, "name": "Sherie3424", "at": "2026-10-20T12:00:00+00:00"}
SEED_RANK = {"rank": 16, "name": "Sherie3424", "at": "2026-10-20T12:00:00+00:00"}


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


def rating_of(duel):
    """The 1v1 rating as an int, or None; the API sends it as text ("2397")."""
    try:
        return int(float(duel.get("skillRating")))
    except (TypeError, ValueError):
        return None


def rank_of(duel):
    """The global 1v1 rank as an int, or None (unranked)."""
    try:
        return int(global_rank(duel.get("rankedStanding")))
    except (TypeError, ValueError):
        return None


def events(state, season, name, rating, rank, seed):
    """An account's new milestones: [(seen key or None, text)]. The key is
    marked seen once posted; None means the all-time bests, kept apart."""
    out = []
    lines = [x for x in RATING_LINES if rating is not None and rating >= x]
    fresh = [x for x in lines if f"{season}:{name}:rating:{x}" not in state["seen"]]
    for x in fresh:
        text = f"Crossed {x}." if x == fresh[-1] else None
        out.append((f"{season}:{name}:rating:{x}", text))
    tiers = [t for t in RANK_TIERS if rank is not None and rank <= t]
    fresh = [t for t in tiers if f"{season}:{name}:top:{t}" not in state["seen"]]
    for t in fresh:
        text = None
        if t == fresh[-1]:
            text = "#1 Global!" if t == 1 else f"Into the global top {t}."
        out.append((f"{season}:{name}:top:{t}", text))
    if seed:  # the first run marks what already stands, silently
        return [(key, None) for key, _ in out]
    return out


def check(state, now, dry_run=False):
    """Fills the queue with one post per account that reached a milestone."""
    seasons = fetch(f"{API}/profile/ranked-seasons")
    season = max(
        s["seasonType"] for s in seasons if isinstance(s.get("seasonType"), int)
    )
    seed = "milestones" not in state
    state["milestones"] = season
    record = state.setdefault("record", dict(SEED_RECORD))
    best = state.setdefault("best_rank", dict(SEED_RANK))
    found = {}
    for name in accounts():
        try:
            duel = standing(name, season)
        except Exception:
            continue
        rating, rank = rating_of(duel), rank_of(duel)
        if rating is None and rank is None:
            continue
        found[name] = (rating, rank, events(state, season, name, rating, rank, seed))
    rated = [(r, n) for n, (r, _, _) in found.items() if r is not None]
    if rated and max(rated)[0] > record["rating"]:
        rating, name = max(rated)
        was = dt.datetime.fromisoformat(record["at"])
        found[name][2].append(
            (
                ("record", {"rating": rating, "name": name, "at": now.isoformat()}),
                f"Our best rating yet (was {record['rating']}, {record['name']}, "
                f"{stamp(was)}).",
            )
        )
    ranked = [(r, n) for n, (_, r, _) in found.items() if r is not None]
    if ranked and min(ranked)[0] < best["rank"]:
        rank, name = min(ranked)
        found[name][2].append(
            (
                ("best_rank", {"rank": rank, "name": name, "at": now.isoformat()}),
                f"Our best global rank yet (was #{best['rank']}, {best['name']}).",
            )
        )
    for name, (rating, rank, news) in found.items():
        texts = [text for _, text in news if text]
        if texts:
            top = rank == 1 and any(text == "#1 Global!" for text in texts)
            head = "🏆" if top else "📈"
            where = f", global #{rank}" if rank is not None else ""
            post = {
                "text": f"{head} **{name}: {rating} rating{where}**, at "
                f"{stamp(now)} Toronto. " + " ".join(texts),
            }
            if not dry_run:
                SHOTS.mkdir(parents=True, exist_ok=True)
                path = SHOTS / f"{name}-{rating}-{rank}-{now:%Y%m%d-%H%M}.png"
                try:
                    screenshot(name, path)
                    post["image"] = str(path)
                except Exception as error:
                    print(f"screenshot failed for {name}: {error}", file=sys.stderr)
                    if top:
                        continue  # #1 needs its picture: try again next run
            state["queue"].append(post)
        for key, _ in news:
            if isinstance(key, tuple):
                state[key[0]] = key[1]
            elif key not in state["seen"]:
                state["seen"].append(key)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true", help="hand over the queue")
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would post; save nothing"
    )
    args = parser.parse_args(argv)
    path = STATE / "rank-watch.json"
    state = read_json(path) or {"seen": [], "queue": []}
    now = dt.datetime.now(dt.timezone.utc)
    try:
        check(state, now, dry_run=args.dry_run)
    except Exception as error:  # colonist unreachable: try next time
        print(f"rank check failed: {error}", file=sys.stderr)
    posts = state["queue"] if args.release or args.dry_run else []
    if args.release:
        state["queue"] = []
    if not args.dry_run:
        path.write_text(json.dumps(state, indent=1))
    print(json.dumps({"posts": posts}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
