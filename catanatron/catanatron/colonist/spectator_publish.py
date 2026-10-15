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
    """Ranked wins and losses against people: today (Toronto) and in all, and
    the last 10 results, oldest first ("WLW..."). Casual games (a new
    account's unlock games) aren't counted."""
    today = dt.datetime.fromtimestamp(now, TORONTO).date()
    counts = {"today": [0, 0], "ranked": [0, 0]}
    queue = None
    recent = ""
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
        if row.get("queue", "ranked") != "ranked":
            continue
        lost = int(row["winner"] != row.get("bot_color"))
        recent = (recent + "WL"[lost])[-10:]
        counts["ranked"][lost] += 1
        if dt.datetime.fromtimestamp(row.get("at", 0), TORONTO).date() == today:
            counts["today"][lost] += 1
    counts = {k: {"wins": w, "losses": l} for k, (w, l) in counts.items()}
    return {**counts, "recent": recent}, queue


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


def _number(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


STAMP = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] ")
EPOCH = re.compile(
    r"^epoch (\d+): .*\| val policy [\d.]+ value ([\d.]+) accuracy ([\d.]+)%(?: aux [\d.]+)? \| (\d+) s"
)
LOCAL_SHARD = re.compile(r"\| (\d+) games, [\d.]+/s$")
MATCH = re.compile(r"^\[[\d:]+\]\s+(\d+) games: ([\d.]+)% ± ([\d.]+)%")


def _when(line, now):
    """A log line's [hh:mm:ss] (UTC) as an ISO time, the latest one not after
    now."""
    match = STAMP.match(line)
    if not match:
        return None
    h, m, sec = map(int, match.groups())
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    stamp = day.replace(hour=h, minute=m, second=sec, microsecond=0)
    if stamp.timestamp() > now + 60:
        stamp -= dt.timedelta(days=1)
    return stamp


def round_progress(lines, number, epochs=3, now=None):
    """The current round's progress, from the end of the training log:
    self-play games, training epochs, the promotion match's running score."""
    now = time.time() if now is None else now
    head = f"] round {number}: "
    first = next((i for i, line in enumerate(lines) if head in line), None)
    if first is None:
        return None
    progress = {"selfplay": None, "training": None, "match": None}
    stamp = None
    previous_local_done = 0
    remote_assigned = 0
    unprepared_remote = False
    for line in lines[first:]:
        if "] round " in line and head not in line:
            break  # the next round
        stamp = _when(line, now) or stamp
        if head + "self-play" in line:
            total = re.search(r"self-play, (\d+) games", line)
            remote_assigned = 0
            unprepared_remote = False
            progress["selfplay"] = {
                "started": stamp.isoformat(),
                "games": int(total.group(1)) if total else None,
                "done": False,
                "localDone": 0,
                "localTotal": None,
            }
        elif progress["selfplay"] and "self-play:" in line and " on " in line:
            remote = re.search(r"self-play: (\d+) games .* on \S+", line)
            if remote:
                remote_assigned += int(remote.group(1))
        elif (
            progress["selfplay"]
            and "could not prepare" in line
            and "range runs here" in line
        ):
            unprepared_remote = True
        elif progress["selfplay"] and "additional games here" in line:
            extra = re.search(r"(\d+) additional games here", line)
            previous_local_done = progress["selfplay"]["localDone"]
            progress["selfplay"]["localTotal"] += int(extra.group(1))
        elif progress["selfplay"] and "games here" in line:
            here = re.search(r"(\d+) games here", line)
            total = progress["selfplay"]["games"]
            progress["selfplay"]["localTotal"] = (
                total - remote_assigned
                if unprepared_remote and total is not None
                else int(here.group(1))
            )
        elif progress["selfplay"] and "shards to play" in line:
            previous_local_done = progress["selfplay"]["localDone"]
        elif progress["selfplay"] and LOCAL_SHARD.search(line):
            progress["selfplay"]["localDone"] = previous_local_done + int(
                LOCAL_SHARD.search(line).group(1)
            )
        elif "self-play complete" in line and progress["selfplay"]:
            progress["selfplay"]["done"] = True
        elif head + "training" in line:
            progress["training"] = {
                "started": stamp.isoformat(),
                "epochs": [],
                "total": epochs,
            }
        elif progress["training"] and EPOCH.match(line):
            e, value, accuracy, seconds = EPOCH.match(line).groups()
            progress["training"]["epochs"].append(
                {
                    "epoch": int(e),
                    "valueLoss": float(value),
                    "accuracy": round(float(accuracy) / 100, 4),
                    "seconds": int(seconds),
                }
            )
        elif head + "promotion match" in line:
            most = re.search(r"at most (\d+) games", line)
            progress["match"] = {
                "started": stamp.isoformat(),
                "maxGames": int(most.group(1)) if most else None,
                "games": 0,
                "score": None,
                "margin": None,
            }
        elif progress["match"] and MATCH.match(line):
            games, score, margin = MATCH.match(line).groups()
            progress["match"].update(
                games=int(games),
                score=round(float(score) / 100, 4),
                margin=round(float(margin) / 100, 4),
            )
    return progress


def training(run, limit=200, now=None):
    """The self-play run's progress, from its state.json: the current round
    and step, and each round's results. The epochs per round come from
    train-config.json (sync_bot.sh writes it from the loop's settings)."""
    path = Path(run) / "state.json"
    state = _read_json(path)
    if not isinstance(state, dict):
        return None
    rolled = set()
    for back in state.get("rollbacks") or []:
        rolled.update(range(int(back["to"]) + 1, int(back["from"]) + 1))
    rounds = []
    for row in (state.get("history") or [])[-limit:]:
        gauntlet = row.get("gauntlet") or {}
        original = next((v for k, v in gauntlet.items() if "round_016" in k), None)
        rounds.append(
            {
                "round": int(row["round"]),
                "games": int(row.get("games") or 0),
                "score": _number(row.get("score")),
                "promoted": bool(row.get("promoted")),
                "rolledBack": int(row["round"]) in rolled,
                "benchmark": _number(row.get("benchmark")),
                "original": _number(original),
                "liveError": _number((row.get("live") or {}).get("mse")),
            }
        )
    current = int(state.get("round", 0))
    config = _read_json(Path(run) / "train-config.json") or {}
    epochs = config.get("epochs")
    epochs = epochs if isinstance(epochs, int) and 0 < epochs < 100 else 3
    progress = None
    try:
        tail = (Path(run) / "train-tail.log").read_text(errors="replace")
        progress = round_progress(tail.splitlines(), current, epochs, now=now)
    except OSError:
        pass
    return {
        "round": current,
        "progress": progress,
        "step": str(state.get("step", ""))[:20],
        "bestRound": int(state.get("best_round", 0)),
        "since": dt.datetime.fromtimestamp(
            path.stat().st_mtime, dt.timezone.utc
        ).isoformat(),
        "rounds": rounds,
        "epochs": epochs,
    }


def net_records(data, names, run="runs/g1"):
    """Ranked wins and losses against people for each of the run's nets
    (labels "g1 round N"; earlier runs' rounds are left out), over this VM's
    bots."""
    prefix = Path(run).name + " round "
    counts = {}
    for account in names:
        try:
            lines = (Path(data) / account / "games.jsonl").read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (
                row.get("own_bot")
                or not row.get("winner")
                or row.get("queue", "ranked") != "ranked"
                or not isinstance(row.get("round"), int)
                or not str(row.get("label", "")).startswith(prefix)
            ):
                continue
            pair = counts.setdefault(row["round"], [0, 0])
            pair[int(row["winner"] != row.get("bot_color"))] += 1
    return [
        {"round": r, "wins": w, "losses": l} for r, (w, l) in sorted(counts.items())
    ][-30:]


def credits(data):
    """The cloud credits left (scripts/credits.py on the owner's Mac, passed
    here by sync_bot.sh), or None."""
    doc = _read_json(Path(data) / "credits.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("credits"), list):
        return None
    rows = []
    for row in doc["credits"][:6]:
        rows.append(
            {
                "provider": str(row.get("provider", ""))[:12],
                "remaining": _number(row.get("remaining")),
                "total": _number(row.get("total")),
                "asOf": row.get("asOf") if isinstance(row.get("asOf"), str) else None,
                "source": "console" if row.get("source") == "console" else "api",
                "currency": "CAD" if row.get("currency") == "CAD" else "USD",
            }
        )
    return rows


def retired_status(folder, name, now=None):
    """A retired account: its record, kept on the site, and no live state."""
    now = time.time() if now is None else now
    runner = _read_json(folder / "runner-status.json") or {}
    counts, queue = record(folder, now)
    return {
        "name": name,
        "status": "retired",
        "queue": queue if queue in ("ranked", "casual") else None,
        "net": runner.get("label") if isinstance(runner.get("label"), str) else None,
        "gameId": None,
        **counts,
    }


def orchestrator(data):
    """The orchestrator's latest report (scripts/vm/orchestrator, passed here
    by sync_bot.sh as data/orchestrator.json), or None."""
    doc = _read_json(Path(data) / "orchestrator.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("summary"), str):
        return None
    try:
        at = dt.datetime.fromisoformat(str(doc.get("at"))).isoformat()
    except ValueError:
        return None
    questions = []
    for q in doc.get("questions") or []:
        if isinstance(q, dict) and re.fullmatch(r"[a-z0-9-]{1,40}", str(q.get("id"))):
            questions.append({"id": q["id"], "text": str(q.get("text", ""))[:1000]})
    return {"at": at, "summary": doc["summary"][:8000], "questions": questions[:10]}


def fetch_answers(site, key, data):
    """The owner's replies from the site's reply box, to data/
    orchestrator-answers.json, which sync_bot.sh brings to the orchestrator."""
    request = urllib.request.Request(
        site.rstrip("/") + "/api/answers",
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        answers = json.load(response).get("answers", [])
    path = Path(data) / "orchestrator-answers.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(answers, indent=1))
    tmp.replace(path)
    return len(answers)


def snapshot(data, names, source, now=None, run="runs/g1", retired=None):
    """This VM's bots, then its retired accounts (whose games stay in their
    folders), with the training run and credits."""
    retired = retired or {}
    bots = []
    for folder in sorted(Path(data).iterdir()):
        if folder.is_dir() and (folder / "runner-status.json").exists():
            if folder.name in retired:
                bots.append(retired_status(folder, retired[folder.name], now))
            elif names.get(folder.name):
                bots.append(bot_status(folder, names[folder.name], now))
    return {
        "schema": 2,
        "source": source,
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "bots": bots,
        "training": training(run),
        "netRecords": net_records(data, {**names, **retired}, run),
        "credits": credits(data),
        "orchestrator": orchestrator(data),
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
    parser.add_argument(
        "--retired",
        default=os.environ.get("COLONIST_RETIRED_NAMES", ""),
        help="account=colonist name of retired accounts, shown with their record",
    )
    parser.add_argument("--run", default="runs/g1", help="the self-play run shown")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    key = Path(args.key_file).read_text().strip()
    names = parse_names(args.names)
    retired = parse_names(args.retired)
    source = re.sub(r"[^a-z0-9-]", "-", args.source.lower())[:32]
    cycle = 0
    while True:
        if cycle % 10 == 0:  # replies every few minutes are plenty
            try:
                fetch_answers(args.site, key, args.data)
            except (OSError, ValueError, urllib.error.HTTPError) as exc:
                print(f"answers retry: {type(exc).__name__}", flush=True)
        cycle += 1
        body = snapshot(args.data, names, source, run=args.run, retired=retired)
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
