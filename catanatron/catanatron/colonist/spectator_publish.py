"""Publish each bot account's status, with a colonist link to its live game.

Runs on every bot VM (`colonist-spectator`). For each account folder in DATA
(data/ranked, data/bot2, ...) it posts: the bot's colonist name, what it is
doing, the net it plays, its record, and the colonist game id while it plays.
Raw captures, opponents, chat and cards never leave the machine.

    python -m catanatron.colonist.spectator_publish --source vm1 \
        --key-file data/.spectator-ingest-key \
        --site https://colonist-ai-spectator.vercel.app
"""

import argparse
import datetime as dt
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
GAME_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")
# The bridge writes the state after every move; a game with no write for
# this long is over or stuck, not live.
LIVE_SECONDS = 180


def parse_names(text):
    """'ranked=CatBot77,bot2=Ericha8499' -> {'ranked': 'CatBot77', ...}"""
    names = {}
    for item in (text or "").split(","):
        account, _, name = item.partition("=")
        if account.strip() and name.strip():
            names[account.strip()] = name.strip()
    return names


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def live_game(folder, now):
    """The colonist game id the account is playing now, or None."""
    path = folder / "spectator-state.json"
    try:
        if now - path.stat().st_mtime > LIVE_SECONDS:
            return None
    except OSError:
        return None
    doc = _read_json(path) or {}
    game = doc.get("colonistGame")
    if (doc.get("live") or {}).get("phase") != "live" or not game:
        return None
    return str(game) if GAME_ID.match(str(game)) else None


def record(folder, now):
    """Wins and losses against people: today (Toronto) and in ranked."""
    today = dt.datetime.fromtimestamp(now, TORONTO).date()
    counts = {"today": [0, 0], "ranked": [0, 0]}
    queue = None
    try:
        lines = (folder / "games.jsonl").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        queue = row.get("queue", queue)
        if row.get("own_bot") or not row.get("winner"):
            continue
        lost = int(row["winner"] != row.get("bot_color"))
        if row.get("queue", "ranked") == "ranked":
            counts["ranked"][lost] += 1
        if dt.datetime.fromtimestamp(row.get("at", 0), TORONTO).date() == today:
            counts["today"][lost] += 1
    return {k: {"wins": w, "losses": l} for k, (w, l) in counts.items()}, queue


def bot_status(folder, name, now=None):
    now = time.time() if now is None else now
    runner = _read_json(folder / "runner-status.json") or {}
    game = live_game(folder, now)
    if (folder / "NEEDS_OWNER.md").exists() or runner.get("state") == "needs_attention":
        status, game = "paused", None
    elif not _alive(runner.get("pid")):
        status, game = "offline", None
    elif game:
        status = "playing"
    else:
        status = "searching"
    counts, queue = record(folder, now)
    return {
        "name": name,
        "status": status,
        "queue": queue if queue in ("ranked", "casual") else None,
        "net": runner.get("label") if isinstance(runner.get("label"), str) else None,
        "gameId": game,
        **counts,
    }


def snapshot(data, names, source, now=None):
    bots = []
    for folder in sorted(Path(data).iterdir()):
        if folder.is_dir() and (folder / "runner-status.json").exists():
            name = names.get(folder.name)
            if name:
                bots.append(bot_status(folder, name, now))
    return {
        "schema": 2,
        "source": source,
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "bots": bots,
    }


def publish(endpoint, key, body):
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/ingest",
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data")
    parser.add_argument("--key-file", default="data/.spectator-ingest-key")
    parser.add_argument("--site", required=True)
    parser.add_argument(
        "--source", default=socket.gethostname(), help="this VM's name on the site"
    )
    parser.add_argument(
        "--names",
        default=os.environ.get("COLONIST_BOT_NAMES", ""),
        help="account=colonist name, comma-separated; other accounts aren't shown",
    )
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    key = Path(args.key_file).read_text().strip()
    names = parse_names(args.names)
    source = re.sub(r"[^a-z0-9-]", "-", args.source.lower())[:32]
    while True:
        body = snapshot(args.data, names, source)
        try:
            publish(args.site, key, body)
            playing = sum(b["status"] == "playing" for b in body["bots"])
            print(f"published {len(body['bots'])} bots, {playing} playing", flush=True)
        except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as exc:
            print(
                f"publish retry: {type(exc).__name__}: {getattr(exc, 'code', '')}",
                flush=True,
            )
        if args.once:
            break
        time.sleep(max(args.interval, 15))


if __name__ == "__main__":
    main()
