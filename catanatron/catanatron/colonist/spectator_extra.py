"""The side runs' progress for the spectator site, written on the coordinator.

sync_bot.sh runs this every few minutes and copies the result to the bot VMs,
whose spectator_publish sends it with their bots:
  - the people model: the last 48 hourly refreshes (opponent_model.py refresh
    appends each verdict to models/human/refresh-history.jsonl);
  - E54 (research's league run): its iterations, the raw score against
    round 73 every 10 iterations, and the searched gates.

    python -m catanatron.colonist.spectator_extra --out data/spectator-extra.json
"""

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
REFRESHES = 48


def _json_lines(path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _score(scores, key, field):
    value = (scores.get(key) or {}).get(field)
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def people_model(folder="models/human", limit=REFRESHES):
    """The last refreshes, oldest first, or None if there are none. Before the
    history existed only refresh.json (the latest verdict) was kept."""
    folder = Path(folder)
    verdicts = _json_lines(folder / "refresh-history.jsonl")
    try:
        latest = json.loads((folder / "refresh.json").read_text())
    except (OSError, ValueError):
        latest = None
    if isinstance(latest, dict) and latest.get("at") not in {
        v.get("at") for v in verdicts if isinstance(v, dict)
    }:
        verdicts.append(latest)
    rows = []
    for verdict in verdicts:
        if not isinstance(verdict, dict) or not isinstance(verdict.get("at"), str):
            continue
        scores = verdict.get("scores") or {}
        rows.append(
            {
                "at": verdict["at"][:32],
                "replaced": bool(verdict.get("replaced")),
                "rows": int((scores.get("new") or {}).get("rows") or 0),
                "newAccuracy": _score(scores, "new", "accuracy"),
                "newCrossEntropy": _score(scores, "new", "cross_entropy"),
                "currentAccuracy": _score(scores, "current", "accuracy"),
                "currentCrossEntropy": _score(scores, "current", "cross_entropy"),
            }
        )
    rows.sort(key=lambda r: r["at"])
    return {"refreshes": rows[-limit:]} if rows else None


STAMP = re.compile(r"^\[(\w{3} \d{1,2}, \d{1,2}:\d\d [AP]M)\] ")
PLAYED = re.compile(r"\] iter (\d+): played (\d+) games in (\d+) s")
EVAL = re.compile(
    r"^(\d+)\t.*round_(\d+)\.npz:\d+: ([\d.]+)% ± ([\d.]+)%.*?; (\d+) games"
)
RESULT = re.compile(
    r"\] ([\w.-]+): \S*iter-(\d+)/net\.npz:\d+ vs \S*round_(\d+)\.npz:\d+: "
    r"([\d.]+)% ± ([\d.]+)%.*?; (\d+) games"
)
POOLED = re.compile(
    r"\] POOLED (\w+?)i(\d+) vs r(\d+) @\d+: \d+-\d+ over (\d+) .*?= "
    r"([\d.]+)% ± ([\d.]+)%"
)


def _when(line, now):
    """A '[Oct 20, 3:27 PM]' (Toronto) stamp as ISO, in the year that puts it
    at most a day after now."""
    match = STAMP.match(line)
    if not match:
        return None
    stamp = dt.datetime.strptime(f"{now.year} {match.group(1)}", "%Y %b %d, %I:%M %p")
    stamp = stamp.replace(tzinfo=TORONTO)
    if stamp > now + dt.timedelta(days=1):
        stamp = stamp.replace(year=now.year - 1)
    return stamp.astimezone(dt.timezone.utc).isoformat()


def _read(path):
    try:
        return Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return []


def e54(folder="data/orchestrator/e54", run="run1", now=None):
    """E54's iterations, evaluations and searched gates, or None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    folder = Path(folder)
    iterations = {}
    for line in _read(folder / run / "loop.txt"):
        played = PLAYED.search(line)
        at = _when(line, now)
        if played and at:
            number = int(played.group(1))
            iterations[number] = {
                "iter": number,
                "games": int(played.group(2)),
                "at": at,
            }
    evals = []
    for line in _read(folder / run / "eval.txt"):
        match = EVAL.match(line)
        if match:
            number, opponent, score, margin, games = match.groups()
            evals.append(
                {
                    "iter": int(number),
                    "opponent": int(opponent),
                    "games": int(games),
                    "score": round(float(score) / 100, 4),
                    "margin": round(float(margin) / 100, 4),
                }
            )
    gates = []
    for line in _read(folder / "searched.txt"):
        at = _when(line, now)
        pooled = POOLED.search(line)
        result = None if pooled else RESULT.search(line)
        if pooled:
            _, number, opponent, games, score, margin = pooled.groups()
            name = f"pooled i{number} vs r{opponent}"
        elif result:
            name, number, opponent, score, margin, games = result.groups()
        else:
            continue
        gates.append(
            {
                "name": name[:40],
                "iter": int(number),
                "opponent": int(opponent),
                "games": int(games),
                "score": round(float(score) / 100, 4),
                "margin": round(float(margin) / 100, 4),
                "pooled": bool(pooled),
                "at": at,
            }
        )
    if not iterations and not evals and not gates:
        return None
    return {
        "iterations": [iterations[k] for k in sorted(iterations)][-200:],
        "evals": evals[-50:],
        "gates": gates[-30:],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="data/spectator-extra.json")
    parser.add_argument("--human", default="models/human")
    parser.add_argument("--e54", default="data/orchestrator/e54")
    args = parser.parse_args(argv)
    doc = {"peopleModel": people_model(args.human), "e54": e54(args.e54)}
    tmp = args.out + ".tmp"
    with open(tmp, "w") as stream:
        json.dump(doc, stream, separators=(",", ":"))
    os.replace(tmp, args.out)  # the sync never copies half a file


if __name__ == "__main__":
    main()
