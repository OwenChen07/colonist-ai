"""Runs the orchestrator's agents for the Discord bot (discord_bot.py):

    agent.py major               a major report (lead model, Opus by default)
    agent.py chat <message-file> answer the owner in #orchestrator (lead model)
    agent.py worker <name>       one task for a workstream (Sonnet by default),
                                 in its own git worktree ~/colonist-ai-wt/<name>

Claude Code by default; Codex when Claude reports its usage limit (or always
with ORCHESTRATOR_AGENT=codex). Each run's cost goes to
data/orchestrator/usage.jsonl. The last line on stdout is JSON for the bot:
{"status": ..., "post": <path of a Markdown file to post, or null>}.

Major and worker runs end with a JSON block (see ORCHESTRATOR.md and
WORKER.md). This script, not the agent, writes what it carries (reports,
journal, progress notes, decisions, messages to workers), so Codex's sandbox
needs no access outside its working directory.
"""

import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402

ROOT = HERE.parents[2]
STATE = ROOT / "data" / "orchestrator"
WORKTREES = Path(
    os.environ.get("ORCHESTRATOR_WORKTREES", Path.home() / "colonist-ai-wt")
)
PY = str(ROOT / ".venv/bin/python")
LEAD_MODEL = os.environ.get("ORCHESTRATOR_LEAD_MODEL", "opus")
WORKER_MODEL = os.environ.get("ORCHESTRATOR_WORKER_MODEL", "sonnet")
TIMEOUT = int(os.environ.get("ORCHESTRATOR_TIMEOUT_S", "3000"))
# Cost charged for a run killed by the timeout, which reports none.
TIMEOUT_COST = float(os.environ.get("ORCHESTRATOR_TIMEOUT_COST_USD", "3"))
LIMIT = re.compile(
    r"usage limit|weekly limit|limit reached|hit your limit|rate limit|out of (credits|usage)",
    re.I,
)
# Ignored by git in the main checkout and shared with every worktree.
SHARED = (".venv", "data", "runs", "models", "captures", "real_replays")
# Modules the agents may run; the live-play ones are left to the bots' services.
LIVE_ONLY = {"__init__", "bridge", "ranked_runner", "live", "client", "capture"}
MODULES = sorted(
    p.stem
    for p in (ROOT / "catanatron/catanatron/colonist").glob("*.py")
    if p.stem not in LIVE_ONLY
)


def allowed_tools(role):
    tools = [
        "Read",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "Agent",
        "Task",
        "TodoWrite",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
        "Bash(.venv/bin/python -m pytest:*)",
        "Bash(.venv/bin/python -m black:*)",
        "Bash(ssh -i ~/.ssh/colonist_worker_ed25519:*)",
        "Bash(scp -i ~/.ssh/colonist_worker_ed25519:*)",
        "Bash(rsync:*)",
        "Bash(scripts/vm/orchestrator/safe_restart_training.sh:*)",
        "Bash(scripts/vm/orchestrator/safe_restart_account.sh:*)",
        "Bash(scripts/vm/bot_ctl.sh:*)",
        "Bash(scripts/vm/sync_bot.sh:*)",
    ]
    for module in MODULES:
        tools.append(f"Bash(.venv/bin/python -m catanatron.colonist.{module}:*)")
        tools.append(f"Bash(nohup .venv/bin/python -m catanatron.colonist.{module}:*)")
    for script in (
        "scripts/vm/orchestrator/context.py",
        "scripts/vm/orchestrator/status.py",
        "scripts/handoff_distributed_training.py",
        "scripts/credits.py",
    ):
        tools.append(f"Bash(.venv/bin/python {script}:*)")
        tools.append(f"Bash(nohup .venv/bin/python {script}:*)")
    for command in (
        "tail head cat ls grep wc df free nproc date mkdir echo sleep "
        "ps pgrep top nvidia-smi sed awk cut sort uniq find du stat diff "
        "uptime which basename dirname realpath readlink"
    ).split():
        tools.append(f"Bash({command}:*)")
    tools += [
        "Bash(systemctl status:*)",
        "Bash(systemctl is-active:*)",
        "Bash(journalctl:*)",
    ]
    if role == "worker":
        tools += [
            "Bash(git add:*)",
            "Bash(git commit:*)",
            "Bash(git checkout:*)",
            "Bash(git restore:*)",
            "Bash(git stash:*)",
            "Bash(git fetch:*)",
            "Bash(git rebase:*)",
            "Bash(scripts/vm/orchestrator/land.sh:*)",
            "Bash(.venv/bin/python -m cProfile:*)",
            "Bash(.venv/bin/python -m pyinstrument:*)",
            "Bash(.venv/bin/python bench/speed.py:*)",
            "Bash(.venv/bin/python bench/selfplay.py:*)",
        ]
    return tools


def now():
    return dt.datetime.now(dt.timezone.utc)


def stamp():
    return now().strftime("%Y%m%d-%H%M%S")


def read(path, limit=None, tail=False):
    try:
        text = Path(path).read_text()
    except OSError:
        return ""
    if limit and len(text) > limit:
        text = ("…" + text[-limit:]) if tail else (text[:limit] + "…")
    return text


def append(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as stream:
        stream.write(text)


def sh(args, cwd=ROOT, check=False):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def final_json(text):
    """The last JSON object in an agent's output (a ```json block or bare)."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    found, i = None, text.find("{")
    while i != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(value, dict):
            found = value
        i = text.find("{", end)
    return found


def claude_blocked():
    data = read(STATE / "claude-blocked.json")
    try:
        until = dt.datetime.fromisoformat(json.loads(data)["until"])
    except (ValueError, KeyError, TypeError):
        return False
    return now() < until


def record_usage(role, workstream, agent, model, cost, started, status):
    row = {
        "at": now().isoformat(timespec="seconds"),
        "day": core.budget_day(now()),
        "role": role,
        "workstream": workstream,
        "agent": agent,
        "model": model,
        "cost_usd": round(cost, 4),
        "minutes": round((time.time() - started) / 60, 1),
        "status": status,
    }
    append(STATE / "usage.jsonl", json.dumps(row) + "\n")


def run_agent(prompt, *, role, workstream=None, model, cwd, log, env=None, resume=None):
    """Runs Claude (Codex on its limit). Returns (text, session_id, agent)."""
    started = time.time()
    agent = os.environ.get("ORCHESTRATOR_AGENT", "claude")
    if agent == "claude" and claude_blocked() and resume is None:
        agent = "codex"
    # land.sh's full test run takes over Bash's default 10 minutes on a busy VM.
    bash_limit = {
        "BASH_DEFAULT_TIMEOUT_MS": "2400000",
        "BASH_MAX_TIMEOUT_MS": "2400000",
    }
    full_env = {**bash_limit, **os.environ, **(env or {})}
    if agent == "claude":
        command = ["claude", "-p", "--model", model, "--output-format", "json"]
        command += ["--permission-mode", "dontAsk"]
        if resume:
            command += ["--resume", resume]
        # The worktrees' data/, runs/ and models/ are symlinks into the main
        # checkout; without it every read of them is denied.
        command += ["--add-dir", str(ROOT)]
        command += ["--allowedTools", *allowed_tools(role)]
        try:
            out = subprocess.run(
                command,
                input=prompt,
                cwd=cwd,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
            )
            raw = out.stdout + out.stderr
        except subprocess.TimeoutExpired as error:
            raw = (error.stdout or b"").decode(errors="replace")
            Path(log).write_text(raw + "\n(timed out)\n")
            record_usage(
                role, workstream, "claude", model, TIMEOUT_COST, started, "timeout"
            )
            return raw, None, "claude"
        Path(log).write_text(raw)
        result = final_json(out.stdout) or {}
        cost = float(result.get("total_cost_usd") or 0)
        limited = bool(LIMIT.search(raw)) and (result.get("is_error") or not result)
        record_usage(
            role,
            workstream,
            "claude",
            model,
            cost,
            started,
            "limit" if limited else "ok",
        )
        if not limited:
            return result.get("result") or "", result.get("session_id"), "claude"
        until = now() + dt.timedelta(hours=1)
        (STATE / "claude-blocked.json").write_text(
            json.dumps({"until": until.isoformat(timespec="seconds")})
        )
        if os.environ.get("ORCHESTRATOR_CODEX_FALLBACK", "1") != "1":
            return "", None, "claude"
        agent = "codex"
    started = time.time()
    command = [
        "codex",
        "exec",
        "--approve-for-me",
        "-c",
        "sandbox_workspace_write.network_access=true",
        prompt,
    ]
    try:
        out = subprocess.run(
            command,
            cwd=cwd,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        raw = out.stdout + out.stderr
        status = "ok"
    except (subprocess.TimeoutExpired, FileNotFoundError) as error:
        raw, status = f"({type(error).__name__})", "failed"
    Path(log).write_text(raw)
    # Codex runs on the ChatGPT plan; its cost doesn't count against Claude's.
    record_usage(role, workstream, "codex", "codex", 0.0, started, status)
    return raw, None, "codex"


def apply_outputs(data, source):
    """Writes decisions and messages to workers carried by a run's JSON.
    Returns how many decisions were filed."""
    known = {d.get("id") for d in core.read_jsonl(STATE / "decisions.jsonl")}
    filed = 0
    for d in data.get("decisions") or []:
        if not isinstance(d, dict) or not d.get("question"):
            continue
        base = d.get("id") or re.sub(r"[^a-z0-9]+", "-", d["question"].lower())[:30]
        ident, n = base, 2
        while ident in known:
            ident, n = f"{base}-{n}", n + 1
        known.add(ident)
        row = {
            "id": ident,
            "from": source,
            "question": d["question"],
            "options": [str(o)[:80] for o in (d.get("options") or [])][:5],
            "urgent": bool(d.get("urgent")),
            "at": now().isoformat(timespec="seconds"),
        }
        append(STATE / "decisions.jsonl", json.dumps(row) + "\n")
        filed += 1
    for workstream, message in (data.get("messages") or {}).items():
        if (STATE / "workstreams" / workstream).is_dir() and message:
            append(
                STATE / "workstreams" / workstream / "inbox.md",
                f"\n### From {source}, {core.local(now()):%b %d %H:%M}\n{message}\n",
            )
    return filed


def workstreams():
    return [p.name for p in sorted((STATE / "workstreams").glob("*")) if p.is_dir()]


def digest():
    """Each workstream's latest state, for the lead."""
    lines = ["## Workstreams"]
    for name in workstreams():
        d = STATE / "workstreams" / name
        lines.append(f"### {name}")
        lines.append(
            "Brief (first lines): " + read(d / "brief.md", 600).replace("\n", " ")
        )
        lines.append(
            "Recent progress notes:\n" + read(d / "progress.md", 2500, tail=True)
        )
        if read(d / "inbox.md").strip():
            lines.append("Unread inbox:\n" + read(d / "inbox.md", 1000))
    rows = core.read_jsonl(STATE / "usage.jsonl")
    t = now()
    lines.append(
        f"## Spend today (API-equivalent): ${core.spent(rows, t):.2f}; "
        + ", ".join(f"{w} ${core.spent(rows, t, w):.2f}" for w in workstreams())
    )
    return "\n".join(lines)


def answers_since(path_seen):
    seen = set(json.loads(read(path_seen) or "[]"))
    rows = core.read_jsonl(STATE / "answers.jsonl")
    new = [r for r in rows if r.get("key") not in seen]
    return new, seen | {r.get("key") for r in rows}


def open_decisions(source=None):
    answered = {r.get("id") for r in core.read_jsonl(STATE / "answers.jsonl")}
    return [
        d
        for d in core.read_jsonl(STATE / "decisions.jsonl")
        if d["id"] not in answered and source in (None, d.get("from"))
    ]


def major():
    tag = stamp()
    runs = STATE / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    sh(["git", "pull", "-q", "--rebase", "--autostash", "origin", "main-9mnmbh"])
    context = sh([PY, str(HERE / "context.py")]).stdout
    new, seen = answers_since(STATE / "answers-seen.json")
    prompt = "\n\n".join(
        [
            read(HERE / "ORCHESTRATOR.md"),
            context,
            digest(),
            "## The owner's answers since your last report\n"
            + ("\n".join(f"- [{a['id']}] {a['answer']}" for a in new) or "- none"),
            "## Decisions still open\n"
            + (
                "\n".join(f"- [{d['id']}] {d['question']}" for d in open_decisions())
                or "- none"
            ),
            "## Your journal (last entries)\n"
            + read(STATE / "journal.md", 6000, tail=True),
        ]
    )
    text, _, agent = run_agent(
        prompt, role="lead", model=LEAD_MODEL, cwd=ROOT, log=runs / f"{tag}-major.log"
    )
    data = final_json(text) or {}
    report = data.get("report")
    if not isinstance(report, str) or not report.strip():
        return {"status": "failed", "post": None, "log": str(runs / f"{tag}-major.log")}
    reports = STATE / "reports"
    reports.mkdir(exist_ok=True)
    when = core.local(now())
    header = f"**{when:%-I %p} report** ({when:%a %b %d}, {agent})\n\n"
    (reports / f"{tag}.md").write_text(header + report)
    (reports / "latest.md").write_text(header + report)
    append(
        STATE / "journal.md",
        f"\n## {when:%Y-%m-%d %H:%M} Toronto ({agent})\n{report}\n\n{data.get('journal', '')}\n",
    )
    if data.get("experiments"):
        append(STATE / "experiments.md", f"\n{data['experiments']}\n")
    apply_outputs(data, "lead")
    (STATE / "answers-seen.json").write_text(json.dumps(sorted(k for k in seen if k)))
    return {"status": "ok", "post": str(reports / f"{tag}.md")}


def chat(message_file):
    tag = stamp()
    chats = STATE / "chat"
    chats.mkdir(parents=True, exist_ok=True)
    message = read(message_file)
    session = {}
    try:
        session = json.loads(read(STATE / "chat-session.json"))
    except ValueError:
        pass
    when = core.local(now())
    # One session per Toronto day keeps the chat's context, and its cost, bounded.
    resume = session.get("id") if session.get("day") == core.budget_day(now()) else None
    if resume:
        prompt = f"The owner, at {when:%-I:%M %p}:\n{message}"
    else:
        status = sh([PY, str(HERE / "status.py")]).stdout
        prompt = "\n\n".join(
            [
                read(HERE / "CHAT.md"),
                status,
                "## The latest major report\n"
                + read(STATE / "reports/latest.md", 4000),
                "## Decisions still open\n"
                + (
                    "\n".join(
                        f"- [{d['id']}] {d['question']}" for d in open_decisions()
                    )
                    or "- none"
                ),
                f"## The owner's message, at {when:%-I:%M %p}\n{message}",
            ]
        )
    text, session_id, agent = run_agent(
        prompt,
        role="lead",
        workstream="chat",
        model=LEAD_MODEL,
        cwd=ROOT,
        log=chats / f"{tag}.log",
        resume=resume,
    )
    if agent == "claude" and session_id:
        (STATE / "chat-session.json").write_text(
            json.dumps({"id": session_id, "day": core.budget_day(now())})
        )
    if agent == "codex":
        text = (final_json(text) or {}).get("reply") or text[-1900:]
    reply = chats / f"{tag}.md"
    reply.write_text(text.strip() or "(no reply; see the log)")
    return {"status": "ok" if text.strip() else "failed", "post": str(reply)}


def worktree(name):
    path = WORKTREES / name
    if not (path / ".git").exists():
        WORKTREES.mkdir(parents=True, exist_ok=True)
        sh(["git", "fetch", "-q", "origin", "main-9mnmbh"], check=True)
        sh(
            [
                "git",
                "worktree",
                "add",
                "-B",
                f"ws/{name}",
                str(path),
                "origin/main-9mnmbh",
            ],
            check=True,
        )
        common = Path(sh(["git", "rev-parse", "--git-common-dir"]).stdout.strip())
        exclude = (
            ROOT / common if not common.is_absolute() else common
        ) / "info/exclude"
        have = read(exclude)
        append(exclude, "".join(f"/{s}\n" for s in SHARED if f"/{s}\n" not in have))
    for shared in SHARED:
        if (ROOT / shared).exists() and not (path / shared).exists():
            (path / shared).symlink_to(ROOT / shared)
    sh(["git", "fetch", "-q", "origin", "main-9mnmbh"], cwd=path)
    clean = not sh(["git", "status", "--porcelain"], cwd=path).stdout.strip()
    unlanded = sh(
        ["git", "rev-list", "origin/main-9mnmbh..HEAD"], cwd=path
    ).stdout.strip()
    if clean and not unlanded:
        # Nothing of its own in flight: start the task from the latest code.
        sh(["git", "reset", "-q", "--hard", "origin/main-9mnmbh"], cwd=path)
    return path


def worker(name):
    tag = stamp()
    d = STATE / "workstreams" / name
    if not d.is_dir():
        seed = HERE / "workstreams" / f"{name}.md"
        d.mkdir(parents=True)
        (d / "brief.md").write_text(read(seed) or f"# {name}\n\n(No brief yet.)\n")
    (d / "runs").mkdir(exist_ok=True)
    path = worktree(name)
    inbox = read(d / "inbox.md")
    prompt = "\n\n".join(
        [
            read(HERE / "WORKER.md"),
            f"# Your workstream: {name}\nWorktree: {path} (branch ws/{name}).\n\n"
            + read(d / "brief.md"),
            "## The lead's latest report\n" + read(STATE / "reports/latest.md", 3000),
            "## Your progress notes (latest last)\n"
            + read(d / "progress.md", 5000, tail=True),
            "## New messages for you (from the owner or the lead)\n"
            + (inbox.strip() or "- none"),
            "## Your questions the owner hasn't answered yet (don't ask again)\n"
            + (
                "\n".join(
                    f"- [{d['id']}] {d['question']}" for d in open_decisions(name)
                )
                or "- none"
            ),
            "## Git state of your worktree\n"
            + sh(["git", "log", "--oneline", "-5"], cwd=path).stdout
            + sh(["git", "status", "--short"], cwd=path).stdout,
        ]
    )
    env = {"PYTHONPATH": f"{path / 'catanatron'}:{path}"}
    log = d / "runs" / f"{tag}.log"
    text, _, agent = run_agent(
        prompt,
        role="worker",
        workstream=name,
        model=WORKER_MODEL,
        cwd=path,
        log=log,
        env=env,
    )
    # Messages that arrived during the task stay for the next one.
    if inbox:
        current = read(d / "inbox.md")
        append(d / "inbox-read.md", inbox)
        (d / "inbox.md").write_text(
            current[len(inbox) :] if current.startswith(inbox) else current
        )
    data = final_json(text) or {}
    status = data.get("status") if data.get("status") in core.COOLDOWN else "failed"
    when = core.local(now())
    note = data.get("progress") or f"(task ended without a summary; see {log.name})"
    append(d / "progress.md", f"\n## {when:%b %d %H:%M} ({agent}, {status})\n{note}\n")
    if apply_outputs(data, name) and status == "progress":
        status = "idle"  # rests until the owner answers, not two minutes
    post = data.get("post")
    if status == "failed" and not post:
        post = f"Task failed or ended without a summary. Log: `{log}`"
    if not post:
        return {"status": status, "post": None}
    out = d / "runs" / f"{tag}.md"
    out.write_text(post)
    return {"status": status, "post": str(out)}


def lock(name):
    """An exclusive lock held until this process exits, or None if taken."""
    handle = open(STATE / f".{name}.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return handle


def main(argv):
    mode = argv[0] if argv else ""
    STATE.mkdir(parents=True, exist_ok=True)
    held = True  # chats are serialized by the bot
    if mode == "major" or (mode == "worker" and len(argv) == 2):
        held = lock(mode if mode == "major" else f"worker-{argv[1]}")
    if held is None:
        print(json.dumps({"status": "busy", "post": None}))
        return 0
    if mode == "major":
        result = major()
    elif mode == "chat" and len(argv) == 2:
        result = chat(argv[1])
    elif mode == "worker" and len(argv) == 2:
        result = worker(argv[1])
    else:
        print(__doc__)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
