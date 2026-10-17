"""The orchestrator's hourly status for Discord: numbers only, no agent, no tokens.

.venv/bin/python scripts/vm/orchestrator/status.py
"""

import csv
import datetime as dt
import glob
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402
from context import ROOT, SITE, STATE, read_json, sh  # noqa: E402


def training():
    state = read_json(ROOT / "runs/g1/state.json") or {}
    line = (
        f"**Training** ({sh('systemctl is-active colonist-train')}): round "
        f"{state.get('round')}, best {state.get('best_round')}, "
        f"{state.get('since_promotion')} since a promotion"
    )
    try:
        with open(ROOT / "runs/g1/log.csv") as stream:
            last = list(csv.DictReader(stream))[-1]
        verdict = (
            "promoted" if str(last.get("promoted")) in ("1", "True", "true") else "kept"
        )
        line += (
            f"; round {last.get('round')} {verdict}, score {last.get('match_score')} "
            f"over {last.get('match_games')} games"
        )
    except (OSError, IndexError):
        pass
    return line


def live():
    ratings = {}
    try:
        with urllib.request.urlopen(SITE + "/api/live", timeout=20) as response:
            for bot in json.load(response).get("bots", []):
                rank = bot.get("rank") or {}
                ratings[bot.get("name")] = (
                    f"{rank.get('rating')} {rank.get('tier') or ''} #{rank.get('globalRank')}"
                )
    except Exception:  # the site is optional
        pass
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    lines = ["**Live** (ranked, last 24 h):"]
    for path in sorted(glob.glob(str(ROOT / "data/*/games.jsonl"))):
        account = Path(path).parent.name
        day = []
        for row in core.read_jsonl(path):
            if (
                row.get("queue", "ranked") == "ranked"
                and not row.get("own_bot")
                and row.get("winner")
                and now - row.get("at", 0) < 86400
            ):
                day.append(row)
        status = (read_json(Path(path).parent / "runner-status.json") or {}).get(
            "state"
        )
        if not day and status != "needs_attention":
            continue  # a retired or idle account
        wins = sum(r["winner"] == r.get("bot_color") for r in day)
        name = next(
            (n for n in ratings if n and n.lower().startswith(account.lower())), None
        )
        rating = f", {ratings[name]}" if name else ""
        flag = "" if status in (None, "running", "starting") else f", ⚠️ {status}"
        lines.append(f"- {account}: {wins}-{len(day) - wins}{rating}{flag}")
    return "\n".join(lines)


def workers():
    states = read_json(STATE / "workers.json") or {}
    rows = core.read_jsonl(STATE / "usage.jsonl")
    now = dt.datetime.now(dt.timezone.utc)
    lines = ["**Workers:**"]
    for name, s in states.items():
        lines.append(
            f"- {name}: {s.get('state')}; ${core.spent(rows, now, name):.2f} today"
        )
    lines.append(f"Spend today: ${core.spent(rows, now):.2f} (API-equivalent)")
    answered = {r.get("id") for r in core.read_jsonl(STATE / "answers.jsonl")}
    waiting = [
        d for d in core.read_jsonl(STATE / "decisions.jsonl") if d["id"] not in answered
    ]
    if waiting:
        lines.append(f"Decisions waiting for you: {len(waiting)}")
        for d in waiting[-5:]:
            lines.append(decision_line(d))
        if len(waiting) > 5:
            lines.append(f"… and {len(waiting) - 5} more")
    return "\n".join(lines)


def decision_line(d):
    """One short line per waiting decision, for a phone screen."""
    try:
        when = f"{core.local(dt.datetime.fromisoformat(d['at'])):%-I:%M %p}"
    except (KeyError, TypeError, ValueError):
        when = "?"
    question = " ".join(str(d.get("question", "")).split())
    if len(question) > 160:
        question = question[:159].rstrip() + "…"
    return f"- **{d['id']}** ({d.get('from')}, {when}): {question}"


def main():
    when = core.local(dt.datetime.now(dt.timezone.utc))
    print(f"**{when:%-I %p} status**")
    for section in (training, live, workers):
        print(section())
    return 0


if __name__ == "__main__":
    sys.exit(main())
