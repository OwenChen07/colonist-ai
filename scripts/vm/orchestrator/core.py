"""The Discord orchestrator's rules: when to report, when a workstream may start
a task, and how its token budget is counted. Kept apart from the bot so the
tests can check them without Discord.

All times are Toronto time (the VMs run on UTC):
- major reports at 9 am, 3 pm and 9 pm (the lead agent, Opus);
- a templated status every hour in between (no LLM);
- from 9 pm to 9 am nothing is posted except account risks; decisions wait
  for the 9 am report, while the workers keep going.
"""

import datetime as dt
import re
import json
from pathlib import Path
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
MAJOR_HOURS = (9, 15, 21)
DAY_START, DAY_END = 9, 21
MAJOR_EARLY = dt.timedelta(minutes=25)
# A workstream rests this long after a task, by how the task ended.
COOLDOWN = {
    "progress": dt.timedelta(minutes=2),
    "idle": dt.timedelta(hours=3),
    "blocked": dt.timedelta(hours=12),
    "failed": dt.timedelta(minutes=30),
    # A copy of this worker was still running (after the bot restarted).
    "busy": dt.timedelta(minutes=5),
}


def local(now):
    return now.astimezone(TORONTO)


def due(now):
    """The (kind, key) posts due at `now`; the bot makes each key once.

    A major report starts MAJOR_EARLY before its hour, so it lands on time.
    """
    t = local(now)
    out = []
    if DAY_START < t.hour < DAY_END and t.hour not in MAJOR_HOURS:
        out.append(("hourly", t.strftime("%Y-%m-%d %H")))
    ahead = local(now + MAJOR_EARLY)
    if ahead.hour in MAJOR_HOURS:
        out.append(("major", ahead.strftime("%Y-%m-%d %H")))
    return out


def daytime(now):
    """True while the owner wants messages: 9:00 am to 9:59 pm."""
    return DAY_START <= local(now).hour <= DAY_END


def budget_day(now):
    return local(now).strftime("%Y-%m-%d")


def read_jsonl(path):
    rows = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def spent(rows, now, workstream=None):
    """Dollars (API-equivalent) spent on the Toronto day of `now`."""
    day = budget_day(now)
    total = 0.0
    for r in rows:
        if r.get("day") != day:
            continue
        if workstream is not None and r.get("workstream") != workstream:
            continue
        total += float(r.get("cost_usd") or 0)
    return total


def worker_budget(daily, lead_reserve, workstreams):
    """Each workstream's share of the day, after the lead's reserve."""
    if not workstreams:
        return 0.0
    return daily * (1 - lead_reserve) / len(workstreams)


def cooldown_until(ended_at, status):
    return ended_at + COOLDOWN.get(status, COOLDOWN["failed"])


def may_start(
    *,
    now,
    running,
    max_workers,
    paused,
    spent_today,
    budget,
    rest_until,
    claude_blocked_until,
    codex_fallback,
    plan=None,
    plan_5h_max=85,
    plan_7d_max=90,
):
    """Whether a workstream may start a task now, and why not if it may not.
    plan is plan_usage.usage()'s percentages; when it is known it gates
    instead of the dollars, which stay as the fallback."""
    if paused:
        return False, "paused"
    if running >= max_workers:
        return False, "worker slots full"
    if plan:
        if plan["five_hour"] > plan_5h_max or plan["seven_day"] > plan_7d_max:
            return False, "plan nearly used"
    elif spent_today >= budget:
        return False, "daily budget used"
    if rest_until and now < rest_until:
        return False, "resting"
    if claude_blocked_until and now < claude_blocked_until and not codex_fallback:
        return False, "Claude usage limit"
    return True, ""


def chunks(text, size=1990):
    """Split text for Discord's 2000-character messages, at line breaks."""
    out, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                out.append(current)
                current = ""
            out.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            out.append(current)
            current = ""
        current += line
    if current.strip():
        out.append(current)
    return out


def option_lines(options):
    """A decision's options as a numbered list: the message wraps them on a
    phone, where long button labels are cut off."""
    lines = []
    for i, option in enumerate(options or []):
        text = re.sub(r"^\s*recommended:\s*", "", option, flags=re.I)
        star = " ★" if text != option else ""
        lines.append(f"**{i + 1}{star}.** {text}")
    return lines


def button_label(i, option):
    """Short enough to show whole on a phone: the option's number."""
    return f"{i + 1} ★" if re.match(r"\s*recommended:", option, re.I) else str(i + 1)


def chosen(options, reply):
    """The option a reply picks by number ('2', '#2', 'option 2'), else None."""
    match = re.fullmatch(r"\s*(?:option\s*|#)?(\d+)\s*\.?\s*", reply, re.I)
    if match and 1 <= int(match.group(1)) <= len(options or []):
        return options[int(match.group(1)) - 1]
    return None
