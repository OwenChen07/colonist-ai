"""The orchestrator's Discord bot. systemd runs it on the coordinator as
colonist-orchestrator.service, so it keeps going with the owner's laptop off.

- #orchestrator: the major reports (9 am, 3 pm and 9 pm Toronto, by the lead
  agent), an hourly status from 10 am to 8 pm (status.py, no tokens), the
  decisions that need the owner (buttons, or reply to the message), and chat
  with the lead. Commands: !status !budget !workers N !pause W !resume W !run W
  !major !experiments !help.
- One channel per workstream (#efficiency, #training, #live-loss, #openhex): each worker
  posts there when a task ends. Messages there go to that worker's inbox, and
  an idle worker starts on them at once.
- #experiments: every experiment in data/orchestrator/experiments.json, as
  pinned messages the bot edits in place when the file changes (!experiments
  re-renders now). Messages there do nothing else.
- #milestones: rank_watch.py's ranked milestones with screenshots (rating
  lines, top-N ranks, our best rating and rank), also posted in #orchestrator.
  Messages there do nothing.
- From 9 pm to 9 am only account risks (data/*/NEEDS_OWNER.md), urgent
  decisions and ranked milestones are posted. The workers keep working within the daily budget.

Settings come from the environment (see orchestrator.env.example).
"""

import asyncio
import datetime as dt
import glob
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import discord

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402
import plan_usage  # noqa: E402

ROOT = HERE.parents[2]
STATE = ROOT / "data" / "orchestrator"
PY = str(ROOT / ".venv/bin/python")
LEAD = "orchestrator"
EXPERIMENTS = "experiments"
MILESTONES = "milestones"
RANK_EVERY_S = 300  # how often rank_watch.py checks for milestones
FOLLOW_EVERY_S = 10  # how often a restarted bot checks its old workers
EXPERIMENTS_EVERY_S = 120  # #experiments re-renders at most this often
WORKSTREAMS = [
    w.strip()
    for w in os.environ.get(
        "ORCHESTRATOR_WORKSTREAMS", "efficiency,training,live-loss"
    ).split(",")
    if w.strip()
]
DAILY_BUDGET = float(os.environ.get("ORCHESTRATOR_DAILY_BUDGET_USD", "15"))
LEAD_RESERVE = float(os.environ.get("ORCHESTRATOR_LEAD_RESERVE", "0.3"))
CODEX_FALLBACK = os.environ.get("ORCHESTRATOR_CODEX_FALLBACK", "1") == "1"
PLAN_5H_MAX = float(os.environ.get("ORCHESTRATOR_PLAN_5H_MAX", "85"))
PLAN_7D_MAX = float(os.environ.get("ORCHESTRATOR_PLAN_7D_MAX", "90"))
log = logging.getLogger("orchestrator")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, value):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(value, indent=1))
    tmp.replace(path)


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def runs(name):
    """A workstream's run posts (runs/<stamp>.md), oldest first."""
    found = (STATE / "workstreams" / name / "runs").glob("*.md")
    return sorted(p for p in found if re.fullmatch(r"\d{8}-\d{6}\.md", p.name))


def alive(pid):
    """Whether pid is still a worker's agent.py (not a reused pid)."""
    try:
        return b"agent.py\0worker" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


def last_status(name):
    """The status of a workstream's last task, from its progress notes."""
    try:
        notes = (STATE / "workstreams" / name / "progress.md").read_text()
    except OSError:
        notes = ""
    found = re.findall(r"^## .*\(\S+, (\w+)\)$", notes, re.M)
    return found[-1] if found else "progress"


class Orchestrator(discord.Client):
    def __init__(self, guild_id, owner_id):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.guild_id, self.owner_id = guild_id, owner_id
        self.path = STATE / "bot-state.json"
        self.state = load(self.path, {})
        for key, default in (
            ("done", []),
            ("posted", {}),
            ("alerted", []),
            ("rest", {}),
            ("paused", []),
            ("workers", {}),  # workstream -> {"pid", "started"}, across restarts
            # #experiments: {"ids": [...], "mtime": ..., "texts": [...]}
            ("experiments", {}),
        ):
            self.state.setdefault(key, default)
        # The newest run post per workstream; seeded so old posts stay unposted.
        self.state.setdefault(
            "last_run", {n: r[-1].stem for n in WORKSTREAMS if (r := runs(n))}
        )
        self.recovered = False
        self.experiments_checked = False  # stored messages still there?
        self.experiments_rendered = 0.0  # monotonic time of the last render
        self.experiments_lock = asyncio.Lock()
        self.state.setdefault(
            "max_workers", int(os.environ.get("ORCHESTRATOR_MAX_WORKERS", "1"))
        )
        self.channels = {}
        self.running = {}  # workstream -> start time
        self.why = {}
        self.major_running = False
        self.chat_lock = asyncio.Lock()
        self.tasks = set()  # keeps background tasks referenced until they end

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def save(self):
        self.state["done"] = self.state["done"][-60:]
        self.state["alerted"] = self.state["alerted"][-200:]
        save(self.path, self.state)

    # Setup ------------------------------------------------------------------

    async def setup_hook(self):
        self.ticker = asyncio.create_task(self.tick_forever())

    async def on_ready(self):
        guild = self.get_guild(self.guild_id)
        if guild is None:
            log.error("not in guild %s", self.guild_id)
            return
        category = discord.utils.get(guild.categories, name="colonist-ai")
        if category is None:
            category = await guild.create_category("colonist-ai")
        for name in [LEAD, *WORKSTREAMS, EXPERIMENTS, MILESTONES]:
            channel = discord.utils.get(guild.text_channels, name=name)
            if channel is None:
                channel = await guild.create_text_channel(name, category=category)
            self.channels[name] = channel
        log.info("ready in %s with %s", guild.name, list(self.channels))

    async def say(self, name, text, **kwargs):
        channel = self.channels.get(name)
        if channel is None or not text.strip():
            return None
        # Agents write "@owner" where they need the owner's input; nothing else
        # in a report or run post pings them.
        text = text.replace("@owner", f"<@{self.owner_id}>")
        message = None
        parts = core.chunks(text)
        for i, part in enumerate(parts):
            extra = kwargs if i == len(parts) - 1 else {}
            message = await channel.send(part, **extra)
        return message

    async def execute(self, *args, started=None):
        """Runs agent.py or a script; returns its last stdout line as JSON."""
        proc = await asyncio.create_subprocess_exec(
            PY,
            *args,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if started:
            started(proc.pid)
        out, _ = await proc.communicate()
        text = out.decode(errors="replace").strip()
        try:
            return json.loads(text.splitlines()[-1]), text
        except (ValueError, IndexError):
            return {}, text

    # The clock --------------------------------------------------------------

    async def tick_forever(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(30)

    async def tick(self):
        if not self.channels:
            return
        if not self.recovered:
            self.recovered = True
            await self.recover()
        now = utcnow()
        for kind, key in core.due(now):
            if f"{kind} {key}" in self.state["done"]:
                continue
            self.state["done"].append(f"{kind} {key}")
            self.save()
            if kind == "hourly":
                await self.post_status()
            else:
                self.spawn(self.major())
        await self.alerts()
        await self.post_decisions(now)
        await self.post_experiments()
        if now.timestamp() - self.state.get("rank_checked", 0) >= RANK_EVERY_S:
            self.state["rank_checked"] = now.timestamp()
            self.spawn(self.rank_watch(now))
        await self.supervise(now)

    async def rank_watch(self, now):
        """Ranked milestones (rank_watch.py), posted at once, day or night, to
        #orchestrator (owner, Oct 20) and #milestones; never a ping."""
        result, text = await self.execute(str(HERE / "rank_watch.py"), "--release")
        for post in result.get("posts", []):
            image = Path(post.get("image", ""))
            for name in (LEAD, MILESTONES):
                extra = {"file": discord.File(image)} if image.is_file() else {}
                await self.say(name, post["text"], **extra)

    async def post_status(self):
        _, text = await self.execute(str(HERE / "status.py"))
        await self.say(LEAD, text)

    async def major(self):
        if self.major_running:
            return
        self.major_running = True
        try:
            result, text = await self.execute(str(HERE / "agent.py"), "major")
            if result.get("status") == "busy":
                return
            if result.get("post"):
                await self.say(LEAD, Path(result["post"]).read_text())
            else:
                await self.say(
                    LEAD,
                    f"The major report failed. Log: `{result.get('log', text[-300:])}`",
                )
        finally:
            self.major_running = False

    async def alerts(self):
        """Account risks reach the owner at any hour."""
        for path in glob.glob(str(ROOT / "data/*/NEEDS_OWNER.md")):
            key = f"{path}:{int(mtime(path))}"
            if key in self.state["alerted"]:
                continue
            self.state["alerted"].append(key)
            self.save()
            note = Path(path).read_text()[:1500]
            account = Path(path).parent.name
            await self.say(
                LEAD, f"<@{self.owner_id}> ⚠️ **{account} needs you**\n{note}"
            )

    # Decisions --------------------------------------------------------------

    def decisions(self):
        answered = {r.get("id") for r in core.read_jsonl(STATE / "answers.jsonl")}
        return [
            d
            for d in core.read_jsonl(STATE / "decisions.jsonl")
            if d.get("id") not in answered
        ]

    async def post_decisions(self, now):
        for d in self.decisions():
            if d["id"] in self.state["posted"]:
                continue
            if not (core.daytime(now) or d.get("urgent")):
                continue  # waits for the morning
            view = discord.ui.View(timeout=None)
            for i, option in enumerate(d.get("options") or []):
                view.add_item(
                    discord.ui.Button(
                        label=core.button_label(i, option),
                        custom_id=f"d:{d['id']}:{i}"[:100],
                        style=(
                            discord.ButtonStyle.primary
                            if i == 0
                            else discord.ButtonStyle.secondary
                        ),
                    )
                )
            # Owner's rule (Oct 18): anything that needs the owner's input pings
            # them; regular updates never do.
            mention = f"<@{self.owner_id}> "
            text = "\n".join(
                [
                    f"{mention}❓ **Decision** `{d['id']}` (from {d.get('from')})",
                    d["question"],
                    *core.option_lines(d.get("options")),
                    "_Tap a number (★ recommended), or reply in your own words._",
                ]
            )
            message = await self.say(LEAD, text, view=view)
            if message:
                self.state["posted"][d["id"]] = message.id
                self.save()

    def answer(self, decision, text):
        at = utcnow().isoformat(timespec="seconds")
        row = {
            "key": f"{decision['id']}@{at}",
            "id": decision["id"],
            "answer": text,
            "at": at,
            "from": decision.get("from"),
        }
        with open(STATE / "answers.jsonl", "a") as stream:
            stream.write(json.dumps(row) + "\n")
        source = decision.get("from")
        if source in WORKSTREAMS:
            self.to_inbox(
                source,
                f"Owner's answer to decision `{decision['id']}` ({decision['question'][:200]}): {text}",
            )

    async def on_interaction(self, interaction):
        custom = (interaction.data or {}).get("custom_id", "")
        if not custom.startswith("d:"):
            return
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the owner decides.", ephemeral=True
            )
            return
        ident, index = custom[2:].rsplit(":", 1)
        decision = next((d for d in self.decisions() if d["id"] == ident), None)
        if decision is None:
            await interaction.response.send_message("Already answered.", ephemeral=True)
            return
        choice = decision["options"][int(index)]
        self.answer(decision, choice)
        await interaction.response.edit_message(
            content=interaction.message.content + f"\n✅ **{choice}**", view=None
        )

    # #experiments -----------------------------------------------------------

    async def post_experiments(self, force=False):
        """Renders experiments.json into #experiments when the file changed,
        editing the stored messages in place. After a restart (or
        !experiments), re-posts them all if one of them is gone."""
        async with self.experiments_lock:
            await self.render_experiments(force)

    async def render_experiments(self, force):
        channel = self.channels.get(EXPERIMENTS)
        path = STATE / "experiments.json"
        stamp = mtime(path)
        if channel is None or not stamp:
            return
        ids = list(self.state["experiments"].get("ids", []))
        if not self.experiments_checked:
            self.experiments_checked = force = True
            try:
                for i in ids:
                    await channel.fetch_message(i)
            except discord.NotFound:
                for i in ids:
                    try:
                        await channel.get_partial_message(i).delete()
                    except discord.HTTPException:
                        pass
                ids = []
        if not force and stamp == self.state["experiments"].get("mtime"):
            return
        # The file changes often; each render costs Discord's rate limit, so a
        # change waits until the last render is EXPERIMENTS_EVERY_S old.
        if not force and time.monotonic() - self.experiments_rendered < (
            EXPERIMENTS_EVERY_S
        ):
            return
        self.experiments_rendered = time.monotonic()
        old = self.state["experiments"].get("texts", [])
        self.state["experiments"]["mtime"] = stamp
        try:
            entries = json.loads(path.read_text())["experiments"]
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("experiments.json unreadable; waiting for a change")
            self.save()
            return
        updated = core.local(dt.datetime.fromtimestamp(stamp, dt.timezone.utc))
        pages = core.experiment_messages(entries, f"{updated:%b %-d, %-I:%M %p}")
        kept = []
        for n, texts in enumerate(pages):
            embeds = [discord.Embed(description=t, colour=0x2B8A3E) for t in texts]
            if n < len(ids):
                if n < len(old) and old[n] == texts:
                    kept.append(ids[n])  # unchanged: no edit, no rate limit
                    continue
                try:
                    await channel.get_partial_message(ids[n]).edit(
                        content=None, embeds=embeds
                    )
                except discord.NotFound:
                    self.experiments_checked = False  # re-post on the next tick
                    return
                kept.append(ids[n])
                continue
            message = await channel.send(embeds=embeds)
            kept.append(message.id)
            if n == 0:
                try:
                    await message.pin()
                except discord.HTTPException:
                    log.warning("could not pin the #experiments message")
        for i in ids[len(pages) :]:
            try:
                await channel.get_partial_message(i).delete()
            except discord.HTTPException:
                pass
        self.state["experiments"] = {"ids": kept, "mtime": stamp, "texts": pages}
        self.save()

    # Workers ----------------------------------------------------------------

    def to_inbox(self, name, text):
        d = STATE / "workstreams" / name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "inbox.md", "a") as stream:
            stream.write(f"\n### {core.local(utcnow()):%b %d %H:%M}\n{text}\n")

    def rest_until(self, name):
        rest = self.state["rest"].get(name)
        if not rest:
            return None
        inbox = STATE / "workstreams" / name / "inbox.md"
        brief = STATE / "workstreams" / name / "brief.md"
        if max(mtime(inbox), mtime(brief)) > rest["ended"]:
            return None  # new input ends the rest
        return dt.datetime.fromisoformat(rest["until"])

    async def supervise(self, now):
        rows = core.read_jsonl(STATE / "usage.jsonl")
        budget = core.worker_budget(DAILY_BUDGET, LEAD_RESERVE, WORKSTREAMS)
        blocked = load(STATE / "claude-blocked.json", {}).get("until")
        blocked = dt.datetime.fromisoformat(blocked) if blocked else None
        plan = await asyncio.to_thread(plan_usage.usage)
        states = {}
        for name in WORKSTREAMS:
            if name in self.running:
                minutes = int((now - self.running[name]).total_seconds() // 60)
                states[name] = {"state": f"working ({minutes} min)"}
                continue
            rest = self.rest_until(name)
            ok, why = core.may_start(
                now=now,
                running=len(self.running),
                max_workers=self.state["max_workers"],
                paused=name in self.state["paused"],
                spent_today=core.spent(rows, now, name),
                budget=budget,
                rest_until=rest,
                claude_blocked_until=blocked,
                codex_fallback=CODEX_FALLBACK,
                plan=plan,
                plan_5h_max=PLAN_5H_MAX,
                plan_7d_max=PLAN_7D_MAX,
            )
            if ok:
                self.running[name] = now
                self.spawn(self.work(name))
                states[name] = {"state": "starting"}
            else:
                if why == "resting":
                    why = f"resting until {core.local(rest):%-I:%M %p}"
                states[name] = {"state": why}
        save(STATE / "workers.json", states)

    def started(self, name, pid):
        self.state["workers"][name] = {
            "pid": pid,
            "started": self.running[name].isoformat(),
        }
        self.save()

    async def work(self, name):
        status = "failed"
        try:
            result, text = await self.execute(
                str(HERE / "agent.py"),
                "worker",
                name,
                started=lambda pid: self.started(name, pid),
            )
            status = result.get("status", "failed")
            if not await self.post_runs(name) and status == "failed":
                await self.say(name, f"Task failed: `{text[-400:]}`")
        except Exception:
            log.exception("worker %s", name)
        finally:
            self.finished(name, status)

    def finished(self, name, status):
        ended = utcnow()
        self.state["rest"][name] = {
            "ended": ended.timestamp(),
            "until": core.cooldown_until(ended, status).isoformat(),
        }
        self.running.pop(name, None)
        self.state["workers"].pop(name, None)
        self.save()

    async def post_runs(self, name):
        """Posts the workstream's run posts not posted yet; returns how many."""
        last = self.state["last_run"].get(name, "")
        new = [p for p in runs(name) if p.stem > last]
        for path in new:
            await self.say(name, path.read_text())
            self.state["last_run"][name] = path.stem
            self.save()
        return len(new)

    async def recover(self):
        """After a restart: follow the workers still running (instead of
        starting a second copy), and post what the others left."""
        for name in WORKSTREAMS:
            worker = self.state["workers"].get(name)
            if worker and alive(worker["pid"]):
                self.running[name] = dt.datetime.fromisoformat(worker["started"])
                self.spawn(self.follow(name, worker["pid"]))
                continue
            self.state["workers"].pop(name, None)
            await self.post_runs(name)
        self.save()

    async def follow(self, name, pid):
        """Waits for a worker started by an earlier bot, then posts its run."""
        while alive(pid):
            await asyncio.sleep(FOLLOW_EVERY_S)
        try:
            await self.post_runs(name)
        finally:
            self.finished(name, last_status(name))

    # Messages ---------------------------------------------------------------

    async def on_message(self, message):
        if message.author.bot or message.author.id != self.owner_id:
            return
        # Pinning, thread and other system messages arrive in the owner's name;
        # only what they typed is a request (owner's rule, Oct 18).
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        name = getattr(message.channel, "name", "")
        text = message.content.strip()
        if text.startswith("!"):
            await self.command(message, text)
            return
        if name in WORKSTREAMS or name == LEAD:
            text = await self.attachments(message, text)
            if not text:
                return
        if name in WORKSTREAMS:
            self.to_inbox(name, f"From the owner: {text}")
            await message.add_reaction("📥")
            if name in self.running:
                await message.reply(
                    "It's mid-task; it reads this at the start of its next one."
                )
            return
        if name != LEAD:
            return
        ref = message.reference.message_id if message.reference else None
        if ref:
            decision = next(
                (
                    d
                    for d in self.decisions()
                    if self.state["posted"].get(d["id"]) == ref
                ),
                None,
            )
            if decision:
                self.answer(
                    decision, core.chosen(decision.get("options"), text) or text
                )
                await message.add_reaction("✅")
                return
        await self.chat(message, text)

    async def attachments(self, message, text):
        """Saves the message's images and text files to data/orchestrator/chat
        and returns the text with a line per file: an image-only message
        used to arrive empty."""
        paths, skipped = [], []
        stamp = f"{utcnow():%Y%m%d-%H%M%S}"
        for n, attachment in enumerate(message.attachments, 1):
            file = core.attachment_name(
                stamp, n, attachment.filename, attachment.content_type, attachment.size
            )
            if not file:
                skipped.append(attachment.filename)
                continue
            path = STATE / "chat" / file
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                await attachment.save(path)
            except (discord.HTTPException, OSError):
                log.exception("could not save attachment %s", attachment.filename)
                skipped.append(attachment.filename)
                continue
            paths.append(path.resolve())  # the Read tool takes absolute paths
        return core.with_attachments(text, paths, skipped)

    async def chat(self, message, text):
        async with self.chat_lock:
            path = STATE / "chat" / f"in-{utcnow():%Y%m%d-%H%M%S}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            async with message.channel.typing():
                result, raw = await self.execute(
                    str(HERE / "agent.py"), "chat", str(path)
                )
            if result.get("post"):
                await self.say(LEAD, Path(result["post"]).read_text())
            else:
                await self.say(LEAD, f"Chat failed: `{raw[-400:]}`")

    async def command(self, message, text):
        words = text[1:].split()
        cmd, arg = (words + [""])[0].lower(), (words[1] if len(words) > 1 else "")
        if cmd == "status":
            await self.post_status()
        elif cmd == "budget":
            rows = core.read_jsonl(STATE / "usage.jsonl")
            now = utcnow()
            share = core.worker_budget(DAILY_BUDGET, LEAD_RESERVE, WORKSTREAMS)
            plan = await asyncio.to_thread(plan_usage.usage)
            lines = [
                (
                    f"Plan used: {plan_usage.line(plan)} (workers stop above "
                    f"{PLAN_5H_MAX:.0f}% / {PLAN_7D_MAX:.0f}%)"
                    if plan
                    else "Plan usage unknown: the dollar budget gates"
                ),
                f"Today ${core.spent(rows, now):.2f} of ${DAILY_BUDGET:.2f} (API-equivalent)",
            ]
            lines += [
                f"- {w}: ${core.spent(rows, now, w):.2f} of ${share:.2f}"
                for w in WORKSTREAMS
            ]
            lines.append(
                f"- lead and chat: ${core.spent(rows, now, None) - sum(core.spent(rows, now, w) for w in WORKSTREAMS):.2f}"
            )
            lines.append(f"Worker slots: {self.state['max_workers']}")
            await message.reply("\n".join(lines))
        elif cmd == "workers" and arg.isdigit():
            self.state["max_workers"] = int(arg)
            self.save()
            await message.add_reaction("👍")
        elif cmd in ("pause", "resume") and arg in WORKSTREAMS:
            paused = set(self.state["paused"])
            paused.add(arg) if cmd == "pause" else paused.discard(arg)
            self.state["paused"] = sorted(paused)
            self.save()
            await message.add_reaction("👍")
        elif cmd == "run" and arg in WORKSTREAMS:
            self.state["rest"].pop(arg, None)
            self.save()
            await message.add_reaction("👍")
        elif cmd == "major":
            self.spawn(self.major())
            await message.add_reaction("👍")
        elif cmd == "experiments":
            self.experiments_checked = False  # also re-post any that are gone
            await self.post_experiments(force=True)
            await message.add_reaction("👍")
        else:
            await message.reply(
                "`!status` `!budget` `!workers N` `!pause W` `!resume W` `!run W` `!major` `!experiments`; "
                f"workstreams: {', '.join(WORKSTREAMS)}"
            )


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    STATE.mkdir(parents=True, exist_ok=True)
    bot = Orchestrator(
        int(os.environ["DISCORD_GUILD_ID"]), int(os.environ["DISCORD_OWNER_ID"])
    )
    bot.run(os.environ["DISCORD_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
