"""The orchestrator's Discord bot. systemd runs it on the coordinator as
colonist-orchestrator.service, so it keeps going with the owner's laptop off.

- #orchestrator: the major reports (9 am, 3 pm and 9 pm Toronto, by the lead
  agent), an hourly status from 10 am to 8 pm (status.py, no tokens), the
  decisions that need the owner (buttons, or reply to the message), and chat
  with the lead. Commands: !status !budget !workers N !pause W !resume W !run W
  !major !help.
- One channel per workstream (#efficiency, #training, #live-loss): each worker
  posts there when a task ends. Messages there go to that worker's inbox, and
  an idle worker starts on them at once.
- From 9 pm to 9 am only account risks (data/*/NEEDS_OWNER.md) and urgent
  decisions are posted. The workers keep working within the daily budget.

Settings come from the environment (see orchestrator.env.example).
"""

import asyncio
import datetime as dt
import glob
import json
import logging
import os
import sys
from pathlib import Path

import discord

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import core  # noqa: E402

ROOT = HERE.parents[2]
STATE = ROOT / "data" / "orchestrator"
PY = str(ROOT / ".venv/bin/python")
LEAD = "orchestrator"
RANK_EVERY_S = 600  # how often rank_watch.py checks for a #1
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
        ):
            self.state.setdefault(key, default)
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
        for name in [LEAD, *WORKSTREAMS]:
            channel = discord.utils.get(guild.text_channels, name=name)
            if channel is None:
                channel = await guild.create_text_channel(name, category=category)
            self.channels[name] = channel
        log.info("ready in %s with %s", guild.name, list(self.channels))

    async def say(self, name, text, **kwargs):
        channel = self.channels.get(name)
        if channel is None or not text.strip():
            return None
        message = None
        parts = core.chunks(text)
        for i, part in enumerate(parts):
            extra = kwargs if i == len(parts) - 1 else {}
            message = await channel.send(part, **extra)
        return message

    async def execute(self, *args):
        """Runs agent.py or a script; returns its last stdout line as JSON."""
        proc = await asyncio.create_subprocess_exec(
            PY,
            *args,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
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
        if now.timestamp() - self.state.get("rank_checked", 0) >= RANK_EVERY_S:
            self.state["rank_checked"] = now.timestamp()
            self.spawn(self.rank_watch(now))
        await self.supervise(now)

    async def rank_watch(self, now):
        """Screenshots of any account at #1 (rank_watch.py), posted by day."""
        args = ["--release"] if core.daytime(now) else []
        result, text = await self.execute(str(HERE / "rank_watch.py"), *args)
        for post in result.get("posts", []):
            image = Path(post.get("image", ""))
            extra = {"file": discord.File(image)} if image.is_file() else {}
            await self.say(LEAD, post["text"], **extra)

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
                        label=option[:80],
                        custom_id=f"d:{d['id']}:{i}"[:100],
                        style=(
                            discord.ButtonStyle.primary
                            if i == 0
                            else discord.ButtonStyle.secondary
                        ),
                    )
                )
            mention = f"<@{self.owner_id}> " if d.get("urgent") else ""
            text = (
                f"{mention}❓ **Decision** `{d['id']}` (from {d.get('from')})\n{d['question']}\n"
                "_Pick a button, or reply to this message in your own words._"
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

    async def work(self, name):
        try:
            result, text = await self.execute(str(HERE / "agent.py"), "worker", name)
            status = result.get("status", "failed")
            if result.get("post"):
                await self.say(name, Path(result["post"]).read_text())
            elif status == "failed":
                await self.say(name, f"Task failed: `{text[-400:]}`")
        except Exception:
            log.exception("worker %s", name)
            status = "failed"
        finally:
            ended = utcnow()
            self.state["rest"][name] = {
                "ended": ended.timestamp(),
                "until": core.cooldown_until(ended, status).isoformat(),
            }
            self.running.pop(name, None)
            self.save()

    # Messages ---------------------------------------------------------------

    async def on_message(self, message):
        if message.author.bot or message.author.id != self.owner_id:
            return
        name = getattr(message.channel, "name", "")
        text = message.content.strip()
        if text.startswith("!"):
            await self.command(message, text)
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
                self.answer(decision, text)
                await message.add_reaction("✅")
                return
        await self.chat(message, text)

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
            lines = [
                f"Today ${core.spent(rows, now):.2f} of ${DAILY_BUDGET:.2f} (API-equivalent)"
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
        else:
            await message.reply(
                "`!status` `!budget` `!workers N` `!pause W` `!resume W` `!run W` `!major`; "
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
