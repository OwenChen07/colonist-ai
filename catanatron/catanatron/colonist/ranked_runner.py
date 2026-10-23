"""Run ranked 1v1 games in a persistent cloud Chromium profile.

The browser loads the existing bot extension and talks to a local bridge.
After each finished game the bridge's capture is verified and exported into
value-only bot positions and human opening decisions. A failed or incomplete
game halts the queue and leaves the browser open for intervention.

First sign in interactively on the cloud host (for example through an SSH-
tunneled desktop):

    python -m catanatron.colonist.ranked_runner --login-only --profile PROFILE

Then run while the laptop is off:

    python -m catanatron.colonist.ranked_runner --net NET --games 1 \
        --profile PROFILE

With --opponent-model PATH --opponent-mix W (or the OPPONENT_MODEL and
OPPONENT_MIX environment variables) the bridge's search expects human-like
replies from the opponent (see opponent_model.py); it is off by default.

With --games 0 it queues games until stopped. Every verified game is listed in
DATA/games.jsonl with the net that played it (its hash, and the training round
when following --promotions), and the capture itself records the same.

Requires the automation extra and `python -m playwright install --with-deps
chromium`. Never copy a personal browser profile or credentials into this host.
"""

import argparse
import asyncio
import hashlib
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.human_data import export_capture
from catanatron.colonist import own_queue, season_pause, top_pause
from catanatron.colonist.spectator_publish import parse_names


URL = "https://colonist.io/"
PORT = 8765


def promoted_checkpoint(run_dir, pin_round=None):
    """The trainer publishes best_round in an atomically replaced state file.
    A pinned round (--pin-round) is played instead, promoted or not."""
    root = Path(run_dir).expanduser().resolve()
    if pin_round is not None:
        round_number = int(pin_round)
    else:
        state = json.loads((root / "state.json").read_text())
        round_number = int(state["best_round"])
    source = root / "nets" / f"round_{round_number:03d}.npz"
    if round_number < 0 or not source.is_file():
        raise RuntimeError(f"promoted checkpoint is missing: {source}")
    return round_number, source


def pin_round_arg(text):
    """--pin-round: a round number, or empty (the service's unset PIN_ROUND)
    to follow promotions."""
    return int(text) if text.strip() else None


def install_promoted_model(source, destination):
    """Validate and atomically install a promoted checkpoint."""
    import numpy as np

    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        with open(source, "rb") as src, open(destination, "rb") as dst:
            if (
                hashlib.file_digest(src, "sha256").digest()
                == hashlib.file_digest(dst, "sha256").digest()
            ):
                return False
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as tmp:
        temp_path = Path(tmp.name)
        try:
            with open(source, "rb") as src:
                shutil.copyfileobj(src, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    try:
        with np.load(temp_path, allow_pickle=False) as net:
            for key in net.files:
                net[key]
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return True


#: One math-library thread for the bridge, set before numpy loads. The net's
#: products are tiny; on a busy machine extra threads only wait on each other.
#: Measured on the cloud VM during self-play: the graph net's 800 simulations
#: took 10.4 s with the default threads and 0.82 s with one, and colonist rolls
#: for a player who takes too long to start a turn.
ONE_THREAD = {
    k: "1"
    for k in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    )
}


def bridge_env():
    return {**os.environ, **ONE_THREAD}


def opponent_stamp(model, mix):
    """When the opponent model file last changed, or None when it's off: a
    new stamp between games restarts the bridge with the retrained model."""
    if not opponent_args(model, mix):
        return None
    try:
        return os.stat(model).st_mtime_ns
    except OSError:
        return None


def opponent_args(model, mix):
    """The bridge's opponent-model flags: none unless both a model and a
    positive mix are set, so the default bridge command is unchanged."""
    if not model or not mix or float(mix) <= 0:
        return []
    return ["--opponent-model", str(model), "--opponent-mix", str(float(mix))]


async def _bridge(
    net,
    simulations,
    captures,
    log_path,
    spectator_state=None,
    label=None,
    opponent=(),
):
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", PORT)
    except OSError:
        pass
    else:
        writer.close()
        await writer.wait_closed()
        raise RuntimeError(f"port {PORT} is already occupied; stop the other bridge")
    log = open(log_path, "a", buffering=1)
    command = [
        sys.executable,
        "-m",
        "catanatron.colonist.bridge",
        str(net),
        "--play",
        "--simulations",
        str(simulations),
        "--save",
        str(captures),
    ]
    if spectator_state:
        command.extend(("--spectator-state", str(spectator_state)))
    if label:
        command.extend(("--label", label))
    command.extend(opponent)
    command.extend(("--port", str(PORT)))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=log,
        stderr=asyncio.subprocess.STDOUT,
        env=bridge_env(),
    )
    for _ in range(100):
        if process.returncode is not None:
            log.close()
            raise RuntimeError("bridge exited during startup; inspect its log")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", PORT)
            writer.close()
            await writer.wait_closed()
            return process, log
        except OSError:
            await asyncio.sleep(0.1)
    process.terminate()
    await process.wait()
    log.close()
    raise RuntimeError("bridge did not start; inspect its log")


async def _page(context):
    page = next((p for p in context.pages if p.url.startswith(URL)), None)
    if page is None:
        page = await context.new_page()
    await page.goto(URL)
    return page


async def queue_ranked_1v1(page):
    await queue_1v1(page, "Ranked")


async def queue_1v1(page, mode="Ranked"):
    """Queue for 1v1 in `mode` ("Ranked" or "Casual"), using only visible
    controls; fail if login or UI changed."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    ranked = page.get_by_role("button", name=re.compile(mode))
    try:
        await ranked.wait_for(timeout=30000)
    except PlaywrightTimeoutError:
        play = page.get_by_text("Play Online", exact=True)
        if await play.count() != 1:
            raise RuntimeError("could not find Colonist's game-mode selector")
        if await play.is_visible():
            await play.click()
        else:
            # The signed-in home page can finish mounting its ranked control
            # after the hidden landing-page link appears in the DOM.
            await ranked.wait_for(timeout=30000)
    await ranked.wait_for(timeout=30000)
    await ranked.click()
    one_on_one = page.get_by_role("heading", name="1v1", exact=True)
    if await one_on_one.count() != 1:
        raise RuntimeError(f"{mode.lower()} 1v1 mode is not available")
    await one_on_one.click()
    if await page.get_by_text("Login to play", exact=True).is_visible():
        raise RuntimeError("cloud browser is not signed in to Colonist")
    start = page.get_by_role("button", name="Start Game", exact=True)
    if await start.count() != 1 or not await start.is_enabled():
        raise RuntimeError(f"{mode.lower()} 1v1 start control is unavailable")
    await start.click()


def round_label(run_dir, round_number):
    return f"{Path(run_dir).name} round {round_number}"


def file_stamp(path):
    """When a file last changed, or None if it's missing."""
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def fixed_net_label(path, name=None):
    """The label of a fixed net (--net-path): its name and the file's date in
    Toronto time, e.g. "people model, Oct 19, 4:50 PM", so its games are told
    apart in games.jsonl and the spectator state."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    when = datetime.fromtimestamp(
        os.stat(path).st_mtime, ZoneInfo("America/Toronto")
    ).strftime("%b %d, %I:%M %p")
    when = when.replace(" 0", " ")
    return f"{name or Path(path).name}, {when}"


def _winner(record):
    """The winner on points, or else the player who didn't resign (or leave)."""
    if record.get("winner"):
        return record["winner"]
    others = [c for c in record.get("seats", []) if c != record.get("resigned")]
    return others[0] if record.get("resigned") and len(others) == 1 else None


def record_game(
    data,
    path,
    conversion,
    net,
    label,
    round_number,
    simulations,
    account=None,
    queue="ranked",
    own_bot=False,
    opponent_mix=0.0,
    pinned=False,
    fixed_net=False,
):
    """Appends one verified game to DATA/games.jsonl: which net played it and
    how it ended. Names and chat stay in the capture."""
    from catanatron.colonist.bridge import net_info

    record = conversion.record
    you = [c for c, p in record.get("players", {}).items() if p.get("you")]
    row = {
        "capture": Path(path).name,
        "net_sha256": net_info(str(net), simulations, True).get("sha256"),
        "label": label,
        "round": round_number,
        "simulations": simulations,
        "bot_color": you[0] if len(you) == 1 else None,
        "winner": _winner(record),
        "ended": conversion.ended,
        "at": int(time.time()),
        "queue": queue,
    }
    if account:
        row["account"] = account
    if opponent_mix:
        # Played expecting human-like replies (bridge --opponent-mix).
        row["opponent_mix"] = opponent_mix
    if pinned:
        # A round chosen for this account (--pin-round), not the promoted one.
        row["pinned"] = True
    if fixed_net:
        # A fixed net file (--net-path, e.g. the people model), not a
        # training round; the label names it.
        row["fixed_net"] = True
    if own_bot:
        # Against another of our own bots: not a human game, so not training
        # data, and not part of the bot's record against people.
        row["own_bot"] = True
    with open(Path(data) / "games.jsonl", "a") as stream:
        stream.write(json.dumps(row) + "\n")
    return row


def frame_cap_script(fps):
    """Page script that lets the game draw at most `fps` frames a second.

    Without a GPU, Chromium draws colonist's WebGL board in software at 60
    frames a second: on the cloud VM that was about 3.5 cores, for pictures
    nobody watches. Moves and game updates come over the WebSocket, which this
    leaves alone; only animation frames are spaced out."""
    interval = 1000 / fps
    return f"""(() => {{
  const raf = window.requestAnimationFrame.bind(window);
  let last = 0;
  window.requestAnimationFrame = (callback) => raf((now) => {{
    if (now - last < {interval:.1f}) {{
      return window.requestAnimationFrame(callback);
    }}
    last = now;
    callback(now);
  }});
}})();"""


def _finished_ranked_capture(path, ranked=True):
    conversion = convert(load_capture(str(path)))
    if bool(conversion.record.get("source", {}).get("ranked")) is not ranked:
        raise ValueError(f"capture is not a {'ranked' if ranked else 'casual'} game")
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        raise ValueError("game capture is incomplete")
    return conversion


UNLOCK = re.compile(r"(\d+)/(\d+) Casual games to unlock")


def ranked_locked_from_text(text):
    """True while the home page still shows "N/M Casual games to unlock",
    False once it doesn't (ranked open), None if the page has no game modes."""
    if "Casual" not in text or "Ranked" not in text:
        return None
    return bool(UNLOCK.search(text))


async def ranked_locked(page):
    try:
        await page.get_by_role("button", name=re.compile("Casual")).wait_for(
            timeout=30000
        )
        return ranked_locked_from_text(await page.locator("body").inner_text())
    except Exception:
        return None


async def queue_with_retries(page, mode, attempts=3):
    """queue_1v1, reloading the page between tries: on a busy machine the home
    page sometimes takes more than 30 s to show the game modes (seen live)."""
    for attempt in range(attempts):
        try:
            return await queue_1v1(page, mode)
        except Exception as error:
            if attempt == attempts - 1 or "signed in" in str(error):
                raise
            print(f"queueing failed ({error!s:.80}); reloading", flush=True)
            await page.goto(URL)


def next_queue(queue, ranked_after, data, account=None):
    """The queue for the next game. A new colonist account must finish some
    casual games before ranked unlocks: with `ranked_after` set, play casual
    until this account has that many verified casual games, then ranked."""
    if queue == "ranked" or ranked_after is None:
        return queue
    played = 0
    path = Path(data) / "games.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("queue") == "casual" and row.get("account") in (None, account):
                played += 1
    return "ranked" if played >= ranked_after else "casual"


def opponent_name(record):
    """The other player's colonist username (two-player games)."""
    names = [p.get("name") for p in record.get("players", {}).values() if not p["you"]]
    return names[0] if len(names) == 1 else None


def export_training_data(path, data):
    """Export a finished game's value and opponent opening positions. A game
    that can't give one of them (an opponent who leaves before placing
    anything, seen live) is skipped for it rather than stopping the runner."""
    for mode, folder in (
        ("value", "bot_value"),
        ("opponent_openings", "human_openings"),
    ):
        try:
            out, count, _ = export_capture(str(path), str(data / folder), mode=mode)
        except ValueError as error:
            print(f"{mode}: skipped ({error})", flush=True)
            continue
        print(f"{mode}: {count} positions -> {out}", flush=True)


async def _wait_for_capture(
    captures, before, process, bridge_log, log_offset, ranked=True
):
    deadline = time.monotonic() + 2 * 60 * 60
    while True:
        with open(bridge_log, errors="replace") as log:
            log.seek(log_offset)
            new_log = log.read()
            log_offset = log.tell()
        stopped = next(
            (line for line in new_log.splitlines() if "  stopped: " in line), None
        )
        if stopped:
            raise RuntimeError(f"bridge {stopped.strip()}")
        for path in sorted(captures.glob("bridge-*.json")):
            if path not in before:
                # The bridge may first save an in-progress version and rewrite
                # that file when the result arrives.
                try:
                    conversion = convert(load_capture(str(path)))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
                else:
                    if conversion.unsupported:
                        raise RuntimeError(
                            f"capture converter stopped: {conversion.unsupported[0]}"
                        )
                    if bool(conversion.record.get("source", {}).get("ranked")) is not (
                        ranked
                    ):
                        raise RuntimeError(
                            f"new capture is not a {'ranked' if ranked else 'casual'} game"
                        )
                    if conversion.ended in ("win", "resignation"):
                        return path
        if process.returncode is not None:
            raise RuntimeError("bridge exited during the game; inspect its log")
        if time.monotonic() > deadline:
            raise RuntimeError("no complete ranked capture after two hours")
        await asyncio.sleep(5)


def extension_for_port(source, port, folder):
    """The bot extension, pointed at a bridge on `port`. Several bots on one
    machine each run a bridge on their own port; the extension's address is
    fixed in background.js, so each gets a copy with its port."""
    if port == 8765:
        return source
    target = Path(folder) / f"bot-extension-{port}"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("node_modules", "test")
    )
    worker = target / "src" / "background.js"
    text = worker.read_text()
    old = "ws://127.0.0.1:8765"
    if old not in text:
        raise RuntimeError("bot extension has no bridge address to change")
    worker.write_text(text.replace(old, f"ws://127.0.0.1:{port}"))
    return target


async def _run(args):
    from playwright.async_api import async_playwright

    global PORT
    PORT = args.port
    ranked = args.queue == "ranked"
    os.umask(0o077)
    profile = Path(args.profile).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    extension = Path(__file__).resolve().parents[3] / "bot"
    if not (extension / "manifest.json").exists():
        raise RuntimeError(f"bot extension is missing: {extension}")
    extension = extension_for_port(extension, PORT, Path(args.data).resolve())
    captures = Path(args.captures).resolve()
    data = Path(args.data).resolve()
    own_accounts = {
        name.strip()
        for name in (
            args.own_accounts or os.environ.get("COLONIST_OWN_ACCOUNTS", "")
        ).split(",")
        if name.strip()
    }
    bot_names = parse_names(os.environ.get("COLONIST_BOT_NAMES", ""))
    own_name = bot_names.get(args.account or "ranked")
    if args.avoid_own_site and not own_name:
        print("--avoid-own-site: no COLONIST_BOT_NAMES entry; off", flush=True)
    if not own_name:
        print("top-ten pause: no COLONIST_BOT_NAMES entry; off", flush=True)
    top_ten = top_pause.Watch(data, own_name)
    peers = os.environ.get("SEASON_PEERS", "")
    season_high = season_pause.Watch(
        data,
        own_name,
        [p.strip() for p in peers.split(",")] if peers else bot_names.values(),
    )
    if season_high.on:
        print("season pause: on (SEASON_HIGH_PAUSE=1)", flush=True)
    captures.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    process = log = None
    completed = False
    active_game = False
    games_completed = 0
    deadline = time.monotonic() + args.hours * 3600 if args.hours else None
    promotion_round = None
    net_stamp = None
    opponent_model_stamp = opponent_stamp(args.opponent_model, args.opponent_mix)
    label = Path(args.net).name if args.net else None
    async with async_playwright() as playwright:
        if not args.login_only and not args.inspect:
            if not args.net:
                raise RuntimeError("--net must point to a local trained network")
            if args.net_path:
                # A fixed net (the people model): installed like a promotion,
                # and reinstalled between games when the file changes.
                net_stamp = file_stamp(args.net_path)
                install_promoted_model(args.net_path, args.net)
                label = fixed_net_label(args.net_path, args.net_name)
                print(f"using fixed net {label}", flush=True)
            elif args.promotions:
                promotion_round, source = promoted_checkpoint(
                    args.promotions, args.pin_round
                )
                install_promoted_model(source, args.net)
                label = round_label(args.promotions, promotion_round)
                kind = "pinned" if args.pin_round is not None else "promoted"
                print(f"using {kind} training round {promotion_round}", flush=True)
            if not Path(args.net).exists():
                raise RuntimeError("--net must point to a local trained network")
            process, log = await _bridge(
                args.net,
                args.simulations,
                captures,
                data / "bridge.log",
                data / "spectator-state.json",
                label,
                opponent_args(args.opponent_model, args.opponent_mix),
            )
            (data / "runner-status.json").write_text(
                json.dumps(
                    {
                        "state": "starting",
                        "pid": os.getpid(),
                        "label": label,
                        "games_completed": 0,
                        "at": int(time.time()),
                    },
                    indent=2,
                )
            )
        width, height = (int(x) for x in args.viewport.split("x"))
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chromium",
            headless=not args.login_only,
            viewport={"width": width, "height": height},
            args=[
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
            ],
        )
        if args.max_fps and not args.login_only:
            await context.add_init_script(frame_cap_script(args.max_fps))
        try:
            # The extension must be running before the game page opens.
            if not context.service_workers:
                await context.wait_for_event("serviceworker", timeout=15000)
            page = await _page(context)
            if args.login_only:
                print(
                    "Sign in to the dedicated bot account, then press Enter here.",
                    flush=True,
                )
                await asyncio.to_thread(input)
                completed = True
                return
            if args.smoke:
                await page.get_by_text("Colonist AI", exact=True).wait_for(
                    timeout=15000
                )
                workers = [w.url for w in context.service_workers]
                if not any(url.startswith("chrome-extension://") for url in workers):
                    raise RuntimeError("bot extension worker did not load")
                # The extension connects to the bridge only after the first
                # game frame. On the home page its overlay is merely mounted.
                print(
                    "cloud Chromium and bot extension loaded; bridge listening",
                    flush=True,
                )
                completed = True
                return
            if args.inspect:
                print(
                    json.dumps(
                        {
                            "url": page.url,
                            "title": await page.title(),
                            "links": await page.get_by_role("link").all_inner_texts(),
                            "buttons": await page.get_by_role(
                                "button"
                            ).all_inner_texts(),
                            "text": (await page.locator("body").inner_text())[:2500],
                        }
                    ),
                    flush=True,
                )
                from playwright.async_api import TimeoutError as PlaywrightTimeoutError

                mode_button = page.get_by_role(
                    "button", name=re.compile("Ranked" if ranked else "Casual")
                )
                try:
                    await mode_button.wait_for(timeout=8000)
                except PlaywrightTimeoutError:
                    await page.get_by_text("Play Online", exact=True).click()
                print(
                    json.dumps(
                        {
                            "after_play_online": (
                                await page.locator("body").inner_text()
                            )[:2500]
                        }
                    ),
                    flush=True,
                )
                await mode_button.wait_for(timeout=15000)
                await mode_button.click()
                await page.get_by_role("heading", name="1v1", exact=True).click()
                labels = [
                    (await button.inner_text()).strip()
                    for button in await page.get_by_role("button").all()
                ]
                print(json.dumps({f"{args.queue}_1v1_buttons": labels}), flush=True)
                completed = True
                return
            games = itertools.count() if args.games == 0 else range(args.games)
            for game in games:
                if deadline is not None and time.monotonic() >= deadline:
                    print(
                        "overnight time limit reached; no new game queued", flush=True
                    )
                    break
                # Owner's rule: an account in the global top ten stops queueing.
                await top_ten.check(label)
                # Off unless SEASON_HIGH_PAUSE=1: pause at a high near the end.
                await season_high.check(label)
                before = set(captures.glob("bridge-*.json"))
                log_offset = (data / "bridge.log").stat().st_size
                queue = next_queue(args.queue, args.ranked_after, data, args.account)
                if args.ranked_after is not None:
                    # colonist's own counter decides when ranked is open.
                    locked = await ranked_locked(page)
                    if locked is not None:
                        queue = "casual" if locked else "ranked"
                ranked = queue == "ranked"
                if ranked and args.avoid_own_site and own_name:
                    await own_queue.wait_for_own_accounts(
                        args.avoid_own_site, data, own_name, own_accounts, bot_names
                    )
                try:
                    await queue_with_retries(page, "Ranked" if ranked else "Casual")
                    if ranked and args.avoid_own_site and own_name:
                        own_queue.mark_queued(data)
                except RuntimeError as error:
                    if not (ranked and args.ranked_after is not None):
                        raise
                    # Ranked may need more than the expected games to unlock:
                    # play casual for now and try ranked again next game.
                    print(f"ranked unavailable ({error}); playing casual", flush=True)
                    queue, ranked = "casual", False
                    await page.goto(URL)
                    await queue_with_retries(page, "Casual")
                active_game = True
                total = args.games or "unlimited"
                print(f"queued {queue} 1v1 game {game + 1}/{total}", flush=True)
                path = await _wait_for_capture(
                    captures,
                    before,
                    process,
                    data / "bridge.log",
                    log_offset,
                    ranked=ranked,
                )
                conversion = _finished_ranked_capture(path, ranked=ranked)
                active_game = False
                own_bot = opponent_name(conversion.record) in own_accounts
                if own_bot:
                    print(
                        "opponent was one of our own bots: no training data", flush=True
                    )
                else:
                    export_training_data(path, data)
                print(f"verified {queue} capture: {path}", flush=True)
                record_game(
                    data,
                    path,
                    conversion,
                    args.net,
                    label,
                    promotion_round,
                    args.simulations,
                    account=args.account,
                    queue=queue,
                    own_bot=own_bot,
                    pinned=args.pin_round is not None,
                    fixed_net=bool(args.net_path),
                    opponent_mix=(
                        float(args.opponent_mix)
                        if opponent_args(args.opponent_model, args.opponent_mix)
                        else 0.0
                    ),
                )
                games_completed += 1
                (data / "runner-status.json").write_text(
                    json.dumps(
                        {
                            "state": "running",
                            "pid": os.getpid(),
                            "label": label,
                            "games_completed": games_completed,
                            "last_result": conversion.ended,
                            "last_winner": conversion.record.get("winner"),
                            "last_capture": str(path),
                            "at": int(time.time()),
                        },
                        indent=2,
                    )
                )
                if args.games == 0 or game + 1 < args.games:
                    await page.goto(URL)
                    new_round, source = (
                        promoted_checkpoint(args.promotions, args.pin_round)
                        if args.promotions and not args.net_path
                        else (promotion_round, None)
                    )
                    stamp = opponent_stamp(args.opponent_model, args.opponent_mix)
                    new_net_stamp = file_stamp(args.net_path) if args.net_path else None
                    # A rollback to an earlier round counts too; so does a
                    # retrained opponent model (opponent_model.py refresh) or
                    # a retrained fixed net (--net-path).
                    net_changed = new_net_stamp is not None and new_net_stamp != (
                        net_stamp
                    )
                    if (
                        new_round != promotion_round
                        or stamp != opponent_model_stamp
                        or net_changed
                    ):
                        opponent_model_stamp = stamp
                        process.terminate()
                        await process.wait()
                        log.close()
                        process = log = None
                        if new_round != promotion_round:
                            install_promoted_model(source, args.net)
                            label = round_label(args.promotions, new_round)
                        if net_changed:
                            net_stamp = new_net_stamp
                            install_promoted_model(args.net_path, args.net)
                            label = fixed_net_label(args.net_path, args.net_name)
                        process, log = await _bridge(
                            args.net,
                            args.simulations,
                            captures,
                            data / "bridge.log",
                            data / "spectator-state.json",
                            label,
                            opponent_args(args.opponent_model, args.opponent_mix),
                        )
                        changed = (
                            f"promoted training round {new_round}"
                            if new_round != promotion_round
                            else (
                                f"fixed net {label}"
                                if net_changed
                                else "the retrained opponent model"
                            )
                        )
                        promotion_round = new_round
                        print(f"switched to {changed} between games", flush=True)
            completed = True
            (data / "runner-status.json").write_text(
                json.dumps(
                    {
                        "state": "complete",
                        "games_completed": games_completed,
                        "at": int(time.time()),
                    },
                    indent=2,
                )
            )
        except Exception as error:
            # A live game must stay open if the bridge or converter stops.
            status = {
                "state": "needs_attention",
                "error": str(error),
                "games_completed": games_completed,
                "at": int(time.time()),
            }
            if not args.inspect:
                (data / "runner-status.json").write_text(json.dumps(status, indent=2))
            if active_game:
                print(
                    f"needs attention: {error}; browser left open",
                    file=sys.stderr,
                    flush=True,
                )
                while True:
                    await asyncio.sleep(60)
            completed = True
            raise
        finally:
            if completed:
                await context.close()
                if process is not None:
                    process.terminate()
                    await process.wait()
                if log is not None:
                    log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--net", help="network to play, required unless --login-only")
    parser.add_argument(
        "--promotions",
        help="training run to follow; switch to its promoted net between games",
    )
    parser.add_argument(
        "--pin-round",
        type=pin_round_arg,
        default=None,
        help="with --promotions: play this training round instead of the "
        "promoted one (empty: follow promotions), to test a net live",
    )
    parser.add_argument(
        "--net-path",
        default=os.environ.get("NET_PATH") or None,
        help="(or NET_PATH) play this net file instead of a training round, "
        "installed to --net and reloaded between games when it changes; "
        "--promotions and --pin-round are then ignored",
    )
    parser.add_argument(
        "--net-name",
        default=os.environ.get("NET_NAME") or None,
        help="(or NET_NAME) the fixed net's name in its label, e.g. "
        '"people model" (default: the file name)',
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--account",
        help="a name for this bot account: sets the default profile, "
        "captures and data folders (several accounts can run on one machine)",
    )
    parser.add_argument(
        "--queue",
        choices=("ranked", "casual"),
        default="ranked",
        help="which 1v1 queue to play (extra accounts: casual)",
    )
    parser.add_argument(
        "--ranked-after",
        type=int,
        default=None,
        help="with --queue casual: switch to ranked after this many verified "
        "casual games (a new account must play some before ranked unlocks)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help="local bridge port; each bot on a machine needs its own",
    )
    parser.add_argument(
        "--own-accounts",
        help="comma-separated usernames of our bots (or COLONIST_OWN_ACCOUNTS): "
        "games against them are recorded but not used as training data",
    )
    parser.add_argument(
        "--avoid-own-site",
        default=own_queue.site_setting(os.environ.get("AVOID_OWN_SITE")),
        help="the spectator site (or AVOID_OWN_SITE; on by default, 0 turns it "
        "off): before a ranked queue, wait up to 90 s while another of our "
        "accounts is queued (own_queue)",
    )
    parser.add_argument("--captures", default=None)
    parser.add_argument("--data", default=None)
    parser.add_argument("--simulations", type=int, default=800)
    parser.add_argument(
        "--opponent-model",
        default=os.environ.get("OPPONENT_MODEL") or None,
        help="the bridge's --opponent-model (or OPPONENT_MODEL): a net trained "
        "on people's moves, for the search's opponent prior",
    )
    parser.add_argument(
        "--opponent-mix",
        type=float,
        default=float(os.environ.get("OPPONENT_MIX") or 0),
        help="the bridge's --opponent-mix (or OPPONENT_MIX), 0 to 1; 0 is off",
    )
    parser.add_argument(
        "--games", type=int, default=1, help="games to play; 0 for no limit"
    )
    parser.add_argument(
        "--hours", type=float, help="stop queueing new games after this many hours"
    )
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument(
        "--viewport",
        default="1280x720",
        help="browser window size, WxH: fewer pixels to draw in software",
    )
    parser.add_argument(
        "--max-fps",
        type=float,
        default=0,
        help="cap the page's animation frames per second (0: no cap)",
    )
    parser.add_argument(
        "--inspect", action="store_true", help="show ranked controls without queueing"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="check browser, extension, and bridge without queueing",
    )
    args = parser.parse_args(argv)
    # The first account keeps the original folders; others get their own.
    name = args.account or "ranked"
    args.profile = args.profile or f"~/.local/share/colonist-ai/{name}-profile"
    args.captures = args.captures or f"captures/{name}"
    args.data = args.data or f"data/{name}"
    if sum((args.login_only, args.smoke, args.inspect)) > 1:
        parser.error("--login-only, --smoke, and --inspect cannot be combined")
    if args.games < 0 or args.simulations < 1:
        parser.error("--games must be 0 or more and --simulations positive")
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    if not 0 <= args.opponent_mix <= 1:
        parser.error("--opponent-mix must be between 0 and 1")
    if args.opponent_mix > 0 and not args.opponent_model:
        parser.error("--opponent-mix needs --opponent-model")
    if args.opponent_mix > 0 and not Path(args.opponent_model).exists():
        parser.error("--opponent-model must point to a local trained network")
    if args.pin_round is not None and not args.promotions:
        parser.error("--pin-round needs --promotions")
    if args.net_path:
        if not args.login_only and not Path(args.net_path).is_file():
            parser.error("--net-path must point to a local trained network")
        args.pin_round = None
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
