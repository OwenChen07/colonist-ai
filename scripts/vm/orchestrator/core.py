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


# #experiments: experiments.json rendered as embeds. Discord allows 4096
# characters in an embed's text and 6000 over all of a message's embeds.
EXPERIMENT_GROUPS = (
    ("Running now", ("running", "deploying")),
    ("Planned", ("planned",)),
    ("Finished", ("done-kept", "done-reverted", "closed")),
)
FINISHED_WORDS = {"done-kept": "kept", "done-reverted": "reverted", "closed": "closed"}
# The workers write statuses and dates in their own words ("live (step 2)",
# "closed: round 73 pinned", "Oct 20, 8:45 PM: the lead's research item"),
# so a status is grouped by its first word and a date is found anywhere in
# the text (owner's request, Oct 20, 11:13 PM: newest first, which the
# unparsed stamps had defeated).
FINISHED_STARTS = ("done", "closed", "kept", "reverted", "failed", "dropped", "skipped")
PLANNED_STARTS = ("planned", "proposed")
STAMP = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})"
    r"(?:, (\d{1,2}:\d{2} [AP]M))?"
)
EMBED_TEXT = 4000
MESSAGE_TEXT = 5800
MESSAGE_EMBEDS = 10


def toronto_stamp(text):
    """The first 'Oct 18, 6:55 AM' or 'Oct 18' in the text as a datetime for
    sorting (year 2000: the stamps carry none), else None."""
    found = STAMP.search(text or "")
    if not found:
        return None
    month, day, clock = found.groups()
    form = "%Y %b %d" + (", %I:%M %p" if clock else "")
    text = f"2000 {month} {day}" + (f", {clock}" if clock else "")
    try:
        return dt.datetime.strptime(text, form)
    except ValueError:
        return None


def experiment_group(status):
    """'Running now', 'Planned' or 'Finished' for a status in any wording."""
    word = re.split(r"[\s:(,;.-]+", (status or "").strip().lower(), maxsplit=1)[0]
    if word in FINISHED_STARTS:
        return "Finished"
    if word in PLANNED_STARTS:
        return "Planned"
    return "Running now"


def experiment_block(e):
    """One experiment: in full while it runs or is planned, one or two
    lines once finished."""
    status = e.get("status", "")
    head = f"**{e.get('id', '?')} · {e.get('title', '')}**"
    if experiment_group(status) == "Finished":
        ended = (e.get("ended") or "").split(",")[0]
        word = FINISHED_WORDS.get(status, status) + (f" {ended}" if ended else "")
        lines = [f"{head} · _{word}_"]
        if e.get("result"):
            lines.append(e["result"])
    else:
        since = f" since {e['started']}" if e.get("started") else ""
        lines = [head, f"_{e.get('workstream', '')} · {status}{since}_"]
        for label, key in (
            ("What", "what"),
            ("Why", "why"),
            ("Judged by", "metric"),
            ("So far", "result"),
        ):
            if e.get(key):
                lines.append(f"**{label}:** {e[key]}")
    if e.get("next"):
        lines.append(f"**Next:** {e['next']}")
    return "\n".join(lines)


def experiment_messages(entries, updated=""):
    """experiments.json's entries as Discord messages, each a list of embed
    texts: running and deploying first, then planned, then finished, newest
    first within a group. As few messages as the limits allow."""

    def newest(e):
        finished = experiment_group(e.get("status")) == "Finished"
        stamp = toronto_stamp(
            (e.get("ended") or e.get("started") or "")
            if finished
            else (e.get("started") or "")
        )
        number = re.match(r"E(\d+)", e.get("id", ""))
        return (stamp or dt.datetime.min, int(number.group(1)) if number else 0)

    title = f"**🧪 Experiments: {len(entries)}**" + (
        f" (updated {updated})" if updated else ""
    )
    blocks = []
    for name, _ in EXPERIMENT_GROUPS:
        group = [e for e in entries if experiment_group(e.get("status")) == name]
        if not group:
            continue
        group.sort(key=newest, reverse=True)
        # A group starts its own embed, so the groups stand apart; its heading
        # goes with its first entry, never alone at a message's end.
        first = f"__**{name} ({len(group)})**__\n" + experiment_block(group[0])
        blocks.append((True, first))
        blocks += [(False, experiment_block(e)) for e in group[1:]]
    blocks[:1] = [(True, title + "".join("\n\n" + b for _, b in blocks[:1]))]
    messages, size = [], 0
    for fresh, block in blocks:
        block = block if len(block) <= EMBED_TEXT else block[: EMBED_TEXT - 1] + "…"
        embeds = messages[-1] if messages else None
        if embeds and not fresh and len(embeds[-1]) + 2 + len(block) <= EMBED_TEXT:
            if size + 2 + len(block) <= MESSAGE_TEXT:
                embeds[-1] += "\n\n" + block
                size += 2 + len(block)
                continue
        if (
            not embeds
            or len(embeds) >= MESSAGE_EMBEDS
            or size + len(block) > MESSAGE_TEXT
        ):
            messages.append([])
            size = 0
        messages[-1].append(block)
        size += len(block)
    return messages


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


ATTACHMENT_MAX = 8 * 1024 * 1024
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".log",
    ".json",
    ".jsonl",
    ".csv",
    ".py",
    ".yaml",
    ".yml",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def attachment_name(stamp, n, filename, content_type, size):
    """Where an owner's attachment is saved ('<stamp>-<n><ext>'), or None if
    it isn't an image or text file up to ATTACHMENT_MAX."""
    suffix = Path(filename or "").suffix.lower()
    kind = (content_type or "").split(";")[0].strip().lower()
    if size > ATTACHMENT_MAX:
        return None
    if suffix in IMAGE_SUFFIXES | TEXT_SUFFIXES:
        return f"{stamp}-{n}{suffix}"
    if kind.startswith("image/") and kind[6:] in ("png", "jpeg", "gif", "webp"):
        return f"{stamp}-{n}.{'jpg' if kind == 'image/jpeg' else kind[6:]}"
    if kind.startswith("text/"):
        return f"{stamp}-{n}.txt"
    return None


def with_attachments(text, paths, skipped=()):
    """The message text with a line per saved attachment (the reader opens
    the path with its Read tool) and per one left out."""
    lines = [text] if text else []
    lines += [f"[attachment: {p}]" for p in paths]
    lines += [
        f"[attachment not saved (not an image or text file, or over 8 MB): {s}]"
        for s in skipped
    ]
    return "\n".join(lines)


def chosen(options, reply):
    """The option a reply picks by number ('2', '#2', 'option 2'), else None."""
    match = re.fullmatch(r"\s*(?:option\s*|#)?(\d+)\s*\.?\s*", reply, re.I)
    if match and 1 <= int(match.group(1)) <= len(options or []):
        return options[int(match.group(1)) - 1]
    return None
