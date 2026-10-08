"""Publish only redacted ranked telemetry to the public spectator dashboard.

Runs on the ranked VM beside the existing bot, without controlling its browser.
Raw captures, names, chat, and hidden hands never leave the machine.
"""

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import GAME_STATE, convert
from catanatron.colonist.live import LiveGame
from catanatron.colonist.spectator import public_snapshot


USING = re.compile(r"using promoted training round (\d+)")
SWITCH = re.compile(r"switched to promoted training round (\d+)")
VERIFIED = re.compile(r"verified ranked capture: (\S+)")
STAMP = re.compile(r"bridge-(\d{8})-(\d{6})\.json$")


def verified_paths(data):
    """Read runner evidence; only explicitly versioned, verified games count."""
    found = {}
    for log in sorted(data.glob("*run.log"), key=lambda p: p.stat().st_mtime):
        version = None
        for line in log.read_text(errors="replace").splitlines():
            match = USING.search(line) or SWITCH.search(line)
            if match:
                version = int(match.group(1))
            match = VERIFIED.search(line)
            if match and version is not None:
                path = Path(match.group(1))
                if path.is_file():
                    found[path] = version
    return found


def result_for_capture(path, version):
    conversion = convert(load_capture(path))
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return None
    record = conversion.record
    if record.get("source", {}).get("ranked") is not True:
        return None
    bot = next(
        (color for color, player in record["players"].items() if player.get("you")),
        None,
    )
    if not bot:
        return None
    winner = record.get("winner")
    if winner is None and record.get("resigned"):
        winner = next(
            (color for color in record["seats"] if color != record["resigned"]), None
        )
    if not winner:
        return None
    match = STAMP.search(path.name)
    if match:
        when = dt.datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone.utc
        )
    else:
        when = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    return {
        "version": version,
        "result": "win" if bot == winner else "loss",
        "at": when.isoformat(),
    }


def replay_for_capture(path, limit=55):
    """Rebuild checked positions; return public snapshots, never raw frames."""
    frames = load_capture(path)
    start = next((f for f in frames if f.dir == "in" and f.type == GAME_STATE), None)
    if not start:
        return []
    game = LiveGame(start.data["data"]["payload"], start.t)
    snapshots = [public_snapshot(game)]
    for frame in frames:
        if frame.seq <= start.seq:
            continue
        if game.apply(frame):
            if game.error:
                break
            snap = public_snapshot(game)
            if (
                snapshots[-1]["moveNumber"] != snap["moveNumber"]
                or snap["phase"] == "finished"
            ):
                snapshots.append(snap)
    if len(snapshots) <= limit:
        return snapshots
    # Preserve the opening as well as the ending while bounding public payloads.
    indices = [round(i * (len(snapshots) - 1) / (limit - 1)) for i in range(limit)]
    return [snapshots[i] for i in indices]


class Publisher:
    def __init__(self, data, key_file, endpoint):
        self.data = Path(data)
        self.key = Path(key_file).read_text().strip()
        self.endpoint = endpoint.rstrip("/") + "/api/ingest"
        self.results = {}
        self.replay_path = None
        self.replay = []

    def snapshot(self):
        paths = verified_paths(self.data)
        for path, version in paths.items():
            if path not in self.results:
                try:
                    self.results[path] = result_for_capture(path, version)
                except Exception as exc:
                    print(
                        f"Skipping unverifiable capture {path.name}: {type(exc).__name__}",
                        flush=True,
                    )
        games = sorted(
            (r for r in self.results.values() if r), key=lambda r: r["at"], reverse=True
        )
        counts = {}
        for game in games:
            row = counts.setdefault(
                game["version"], {"version": game["version"], "wins": 0, "losses": 0}
            )
            row["wins" if game["result"] == "win" else "losses"] += 1
        latest = max(paths, key=lambda p: p.stat().st_mtime) if paths else None
        if latest and latest != self.replay_path:
            try:
                self.replay = replay_for_capture(latest)
                self.replay_path = latest
            except Exception as exc:
                print(f"Replay unavailable: {type(exc).__name__}", flush=True)
        telemetry = self.data / "spectator-state.json"
        current = None
        if telemetry.is_file() and time.time() - telemetry.stat().st_mtime < 75:
            try:
                doc = json.loads(telemetry.read_text())
                if (
                    doc.get("schema") == 1
                    and doc.get("live", {}).get("phase") == "live"
                ):
                    current = doc
            except (ValueError, OSError):
                pass
        active_version = None
        # The current runner can switch models before its first verified result.
        for log in sorted(self.data.glob("*run.log"), key=lambda p: p.stat().st_mtime):
            for line in log.read_text(errors="replace").splitlines():
                match = USING.search(line) or SWITCH.search(line)
                if match:
                    active_version = int(match.group(1))
        if active_version is None and counts:
            active_version = max(counts)
        frames = current["rewind"] if current else self.replay
        return {
            "schema": 1,
            "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "live": current["live"] if current else frames[-1] if frames else None,
            "rewind": frames,
            "stats": sorted(counts.values(), key=lambda row: row["version"]),
            "recentGames": games[:16],
            "meta": {"activeVersion": active_version, "verifiedGames": len(games)},
        }

    def publish(self):
        snapshot = self.snapshot()
        encoded = json.dumps(snapshot, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
        print(
            f"published {len(snapshot['recentGames'])} recent results; live={snapshot['live'] is not None}; bytes={len(encoded)}",
            flush=True,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default="data/ranked")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--interval", type=int, default=40)
    parser.add_argument(
        "--deadline", help="UTC ISO timestamp; stop publishing at this time"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    deadline = (
        dt.datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
        if args.deadline
        else None
    )
    publisher = Publisher(args.data, args.key_file, args.site)
    while True:
        try:
            publisher.publish()
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            print(
                f"publish retry: {type(exc).__name__}: {getattr(exc, 'code', '')}",
                flush=True,
            )
        if args.once or (deadline and dt.datetime.now(dt.timezone.utc) >= deadline):
            break
        time.sleep(max(args.interval, 15))


if __name__ == "__main__":
    main()
