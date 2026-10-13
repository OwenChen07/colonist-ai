"""The orchestrator's context report: everything an hourly run should know,
gathered cheaply on the coordinator (no agent needed), as Markdown on stdout.

    .venv/bin/python scripts/vm/orchestrator/context.py

Also lists the owner's replies not seen by an earlier run (`--mark-seen`
records them as seen, after a run that handled them).
"""

import argparse
import csv
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "data" / "orchestrator"
TORONTO = ZoneInfo("America/Toronto")
SITE = "https://colonist-ai-spectator.vercel.app"
KEY_LINES = (
    "round ",
    "promoted",
    "kept round",
    "passed:",
    "anchor",
    "gauntlet",
    "live value MSE",
    "guardrail",
    "WARNING",
    "human starts",
    "it failed",
)


def sh(command):
    try:
        out = subprocess.run(
            command, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=120
        )
        return (out.stdout + out.stderr).strip()
    except subprocess.TimeoutExpired:
        return "(timed out)"


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def toronto(ts):
    return dt.datetime.fromtimestamp(ts, TORONTO).strftime("%b %d %H:%M")


def training():
    lines = ["## Training (runs/g1)"]
    state = read_json(ROOT / "runs/g1/state.json") or {}
    lines.append(
        f"- service: {sh('systemctl is-active colonist-train')}, restarts "
        f"{sh('systemctl show -p NRestarts --value colonist-train')}"
    )
    lines.append(
        f"- round {state.get('round')}, step {state.get('step')}, best round "
        f"{state.get('best_round')}, since promotion {state.get('since_promotion')}, "
        f"anchor reference {state.get('anchor')}"
    )
    rows = []
    try:
        with open(ROOT / "runs/g1/log.csv") as stream:
            rows = list(csv.DictReader(stream))[-10:]
    except OSError:
        pass
    if rows:
        lines.append(
            "- last rounds (log.csv): round, promoted, match games, score, best, benchmark"
        )
        for r in rows:
            lines.append(
                f"  - {r.get('round')}: {r.get('promoted')}, {r.get('match_games')}, "
                f"{r.get('match_score')}, best {r.get('best_round')}, {r.get('benchmark')}"
            )
    history = state.get("history") or []
    for record in history[-3:]:
        keep = {
            k: record.get(k)
            for k in (
                "round",
                "score",
                "games",
                "promoted",
                "passed",
                "anchor",
                "best_anchor",
                "guardrails",
                "live",
                "benchmark",
            )
        }
        lines.append(f"- history: {json.dumps(keep)[:900]}")
    try:
        log = (ROOT / "runs/train.log").read_text().splitlines()
    except OSError:
        log = []
    key = [l for l in log if any(k in l for k in KEY_LINES)][-30:]
    lines.append("- key lines of runs/train.log (UTC):")
    lines += [f"  {l[:220]}" for l in key]
    lines.append("- last 6 lines of runs/train.log:")
    lines += [f"  {l[:220]}" for l in log[-6:]]
    refresh = read_json(ROOT / "models/human/refresh.json")
    if refresh:
        lines.append(f"- human opponent model refresh: {json.dumps(refresh)[:600]}")
    return lines


def live():
    lines = ["## Live play (bot accounts)"]
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    lines.append(
        "- services: " + sh("scripts/vm/bot_ctl.sh accounts 2>&1 | tr '\\n' ';'")
    )
    for path in sorted(glob.glob(str(ROOT / "data/*/games.jsonl"))):
        account = Path(path).parent.name
        rows = []
        for line in Path(path).read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        ranked = [
            r
            for r in rows
            if r.get("queue", "ranked") == "ranked"
            and not r.get("own_bot")
            and r.get("winner")
        ]
        if not ranked:
            continue

        def record(rs):
            wins = sum(r["winner"] == r.get("bot_color") for r in rs)
            return f"{wins}-{len(rs) - wins}"

        day = [r for r in ranked if now - r.get("at", 0) < 86400]
        mixed = [r for r in ranked if r.get("opponent_mix")]
        labels = {}
        for r in ranked[-100:]:
            labels[r.get("label") or r.get("net")] = (
                labels.get(r.get("label") or r.get("net"), 0) + 1
            )
        line = (
            f"- {account}: ranked all {record(ranked)}, last 24 h {record(day)}, "
            f"last 50 {record(ranked[-50:])}, last game {toronto(ranked[-1].get('at', 0))}; "
            f"nets in last 100: {labels}"
        )
        if mixed:
            line += f"; with opponent model: {record(mixed)} (since {toronto(mixed[0].get('at', 0))})"
        lines.append(line)
        status = read_json(Path(path).parent / "runner-status.json") or {}
        if status.get("state") not in (None, "running", "starting"):
            lines.append(
                f"  - runner state: {status.get('state')}: {status.get('error', '')}"
            )
    try:
        with urllib.request.urlopen(SITE + "/api/live", timeout=20) as response:
            site = json.load(response)
        for bot in site.get("bots", []):
            rank = bot.get("rank") or {}
            lines.append(
                f"- site: {bot['name']} {bot['status']}, {rank.get('tier')} "
                f"{rank.get('rating')} Elo, global #{rank.get('globalRank')}"
            )
    except Exception as error:  # the site is optional context
        lines.append(f"- site unavailable: {type(error).__name__}")
    for note in glob.glob(str(ROOT / "data/*/NEEDS_OWNER.md")):
        lines.append(
            f"- NEEDS_OWNER: {note} (modified {toronto(os.path.getmtime(note))})"
        )
    return lines


def ops():
    lines = ["## Operations"]
    lines.append(
        "- watchdog: "
        + sh("tail -4 data/watchdog/health.log | cut -c1-200 | tr '\\n' ';'")
    )
    lines.append(
        "- workers: " + sh("grep ^COLONIST_WORKER_HOSTS scripts/vm/workers.env")
    )
    credits = read_json(ROOT / "data/credits.json") or {}
    for c in credits.get("credits", []):
        lines.append(
            f"- credits {c.get('provider')}: {c.get('remaining')} of {c.get('total')} "
            f"{c.get('currency')} (as of {c.get('asOf')})"
        )
    lines.append("- git: " + sh("git log --oneline -6 | tr '\\n' ';'"))
    lines.append("- disk: " + sh("df -h / | tail -1"))
    for pid_file in sorted(glob.glob(str(STATE / "jobs/*.pid"))):
        try:
            pid = int(Path(pid_file).read_text().strip())
            os.kill(pid, 0)
            alive = "running"
        except (OSError, ValueError):
            alive = "finished"
        log = pid_file[:-4] + ".log"
        tail = (
            sh(f"tail -2 {log} | cut -c1-200 | tr '\\n' ';'")
            if os.path.exists(log)
            else ""
        )
        lines.append(f"- job {Path(pid_file).stem}: {alive}; {tail}")
    return lines


def answers(mark_seen=False):
    """New owner replies (from the site, via the bot VMs' publishers)."""
    seen_path = STATE / "answers-seen.json"
    seen = set(read_json(seen_path) or [])
    found = {}
    for path in glob.glob(str(ROOT / "data/orchestrator-answers.json")):
        for a in read_json(path) or []:
            if isinstance(a, dict) and a.get("id"):
                found[a["id"]] = a
    new = [
        a
        for a in sorted(found.values(), key=lambda a: a.get("at", ""))
        if a["id"] not in seen
    ]
    if mark_seen:
        seen_path.write_text(json.dumps(sorted(seen | {a["id"] for a in new})))
        return []
    lines = ["## The owner's new replies (act on these first)"]
    if not new:
        lines.append("- none")
    for a in new:
        lines.append(
            f"- [{a.get('questionId')}] at {a.get('at')}: {a.get('text', '')[:2000]}"
        )
    report = read_json(STATE / "site.json") or {}
    if report.get("questions"):
        lines.append("- questions currently open on the site:")
        lines += [f"  - [{q['id']}] {q['text']}" for q in report["questions"]]
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mark-seen", action="store_true")
    args = parser.parse_args(argv)
    STATE.mkdir(parents=True, exist_ok=True)
    if args.mark_seen:
        answers(mark_seen=True)
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    out = [
        f"# Context report, {now.isoformat(timespec='minutes')} UTC "
        f"({now.astimezone(TORONTO).strftime('%a %b %d %H:%M')} Toronto)",
        "",
    ]
    for section in (answers, training, live, ops):
        out += section() + [""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
