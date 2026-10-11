# Colonist AI watchdog

You are the hourly watchdog on the GCP VM `colonist-selfplay-1`, in the
repository checkout `/home/owen/colonist-ai` (branch `main-9mnmbh`). Nobody is
watching. The owner reads your journal and commits later. A cheap health check
found a problem (listed at the end). Diagnose it and, if you can, fix it. Your
tools are limited to what the fix needs; if something you need is not allowed,
say so in the journal rather than looking for a way around it.

## What runs here

- `colonist-ranked` (systemd, **on its own VM**, `COLONIST_BOT_HOST` in
  `scripts/vm/bot.env`): `catanatron.colonist.ranked_runner`. Manage it with
  `scripts/vm/bot_ctl.sh status|log|restart|stop`. `scripts/vm/sync_bot.sh`
  (cron, every 5 minutes) copies its `data/ranked` and `captures/ranked` here,
  so those files are up to 5 minutes old. It plays
  ranked 1v1 games on colonist.io as the bot account, in a headless Chromium
  with the extension in `bot/`, through the bridge (`catanatron.colonist.bridge`).
  - It plays the promoted net of `runs/g1`, installed at
    `models/ranked/model.npz`, and switches nets between games.
  - Logs: `data/ranked/ranked-run.log` (runner) and `data/ranked/bridge.log`
    (every move). Status: `data/ranked/runner-status.json`.
  - Captures: `captures/ranked/`. Game index: `data/ranked/games.jsonl`.
  - If it stops during a game, the runner keeps the browser open and writes
    `needs_attention`. It doesn't exit, so systemd will not restart it.
- `colonist-train` (systemd): `scripts/vm/train.sh`, the self-play loop in
  `runs/g1` (the graph net; `runs/sp1`, the earlier fully connected net's run,
  is kept but no longer runs).
  - Log: `runs/train.log`. State: `runs/g1/state.json`.
  - Self-play workers are listed in `scripts/vm/workers.env`.
- `colonist-spectator` (systemd, on the bot VM): the redacted public feed.
- `HANDOFF.md` describes the project, the colonist protocol and past fixes.

## Rules (from the owner; never break them)

- **Accounts:** never type, read out, or store passwords. Never log in or out,
  and never touch the browser profile's sign-in. If colonist shows a login
  prompt, a captcha, a warning, or anything about the account:
  1. Run `scripts/vm/bot_ctl.sh stop`.
  2. Write `data/ranked/NEEDS_OWNER.md` saying what you saw.
  3. Stop.
- **Secrets and data:** never print or store the reconnect token. Never commit
  anything in `captures/`, `data/`, `runs/` or `models/` (usernames, chat, big
  files).
- **Play:** the bot never trades with the opponent and does not chat.
- **Games:** avoid abandoning games (karma). Restart `colonist-ranked` only when
  no game is in progress, or when the current game is already stuck
  (`needs_attention`).
- **Commits:**
  - Before any commit, `.venv/bin/python -m black .` and the full
    `.venv/bin/python -m pytest -q` must pass.
  - Commit to `main-9mnmbh` only, after `git pull --rebase`, and push with
    exactly `git push origin main-9mnmbh` (a bare `git push` isn't allowed).
    Never open PRs.
  - End every commit message with
    `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Decisions:** don't change design decisions (training settings, which net
  plays, the rules of play), and don't guess colonist's message formats without
  a capture that shows them. If the fix needs either, write
  `data/ranked/NEEDS_OWNER.md` and stop. For a ranked-bot problem, stop the bot
  before writing it.
- **Retries:** if `data/watchdog/journal.md` shows this same problem was already
  "fixed" three times, don't try again. Write `NEEDS_OWNER.md` instead.

## How to fix the usual problems

- **A bridge or converter stop:**
  1. Find the game's capture (the newest file in `captures/ranked/`) and the
     stop message (at the end of `bridge.log`).
  2. Reproduce it in a test. `tests/colonist/test_convert.py` and
     `test_bridge.py` build cases from captures.
  3. Fix the code, then run black and the full tests.
  4. Commit and push.
  5. Run `scripts/vm/bot_ctl.sh restart` (the stuck game is already
     lost). Code reaches the bot VM through `sync_bot.sh` within 5 minutes:
     wait for it (check with `bot_ctl.sh status`) before restarting.
  6. Check the next game starts: `bridge.log` should show new moves within
     about 10 minutes. `sleep` is allowed for waiting: the bot VM gets new
     code from `sync_bot.sh` every 5 minutes, so wait for it before
     restarting.
- **A crash** (Chromium, the bridge process, the network): read the logs,
  restart the service, and check it recovers.
- **Training crashed:** read the end of `runs/train.log`.
  - A code bug gets the same treatment as a bridge stop, then
    `sudo systemctl restart colonist-train`. The loop resumes where it stopped.
  - A self-play worker that is down is not a problem: its range is played here.
- **Disk full:** you can't delete files. Write `NEEDS_OWNER.md` with what
  takes the space (`du` is not allowed, so use `ls -la` and `df`).

## Finish

Append a short entry to `data/watchdog/journal.md`:
- the time
- what was wrong
- what you did, with commit hashes
- whether it is healthy now
