# colonist-ai: handoff (6 Oct 2026)

For the next agent (Codex) taking over. Read this, then `PLAN.md` (the source of
truth for phases, gates and decisions) and `bot/README.md` (extension + bridge).

## 1. What the project is

A self-play AI for colonist.io **1v1** (15 points, friendly robber, balanced dice,
9-card discard limit), built on a fork of Catanatron (GPL-3). It plays live on
colonist.io through a Chrome extension (`bot/`) and a local Python "bridge".

- Repo: `github.com/OwenChen07/colonist-ai`, **work branch `main-9mnmbh`** (push only
  there, no PRs). Local checkout: `~/programming/colonist-ai` on the owner's MacBook
  Air M4 (10 cores: 4 performance + 6 efficiency, 16 GB, fanless).
- Python env: `.venv/` (Python 3.11). Extras: `.[web,gym,dev,colonist,train]`.

## 2. Rules for working in this repo (owner's requirements)

- Before every commit: `.venv/bin/python -m black .` (the pinned black, not the one on
  PATH) and the full `.venv/bin/python -m pytest -q` must pass (currently 505 tests).
- Commit messages end with: `Co-Authored-By: Claude <noreply@anthropic.com>` (use your
  own attribution line if the owner prefers; ask).
- **Never commit `captures/` or `records/`** (real usernames and chat). Never print or
  store colonist's reconnect token. Test fixtures must hold no names or chat.
- The AI **never trades with the other player** (declines every offer). Bank/port
  trades are normal moves.
- Live play: the owner approved **ranked** 1v1 (8 Oct 2026), from the bot account
  `CatBot77` (signed in by the owner in Chrome, and in the cloud browser profile on
  the VM). Never type passwords or log in/out. **Don't abandon games**
  (closing/reloading a tab mid-game costs account karma; it dropped once to 3/4).
  The owner said not to bother with the "I'm an AI bot" chat notice.
- macOS shell is zsh: quote globs in commands that go to remote machines
  (`'host:/path/shard_*.npz'`), and `for x in "a b"` doesn't word-split.

## 3. Architecture (where things live)

`catanatron/catanatron/colonist/`:
- `rules.py`: colonist 1v1 rules factory (`new_game`).
- `capture.py`, `convert.py`, `record.py`, `replay.py`: colonist WebSocket captures →
  game records (Catanatron actions + card/point checks) → replay check. `convert.py`
  holds the whole colonist message/log mapping (log entry types, skip list
  `LOG_IGNORED`, coordinates).
- `live.py`: follows a live game from colonist messages (uses the converter).
- `client.py`: encodes the bot's moves as colonist client messages.
- `bridge.py`: WebSocket server the extension talks to; follows the game, runs the
  search for the bot's seat, sends moves (`--play`), confirms each move, declines trade
  offers, saves every game to `captures/bridge-<time>.json`.
- `encode.py`, `net.py`, `mcts.py` (Gumbel MCTS with chance nodes; `NetPlayer`,
  registry key `Z:<net.npz>:<simulations>`), `datagen.py`, `train.py`, `selfplay.py`,
  `loop.py` (self-play training loop), `evaluate.py` (paired-board match harness + SPRT).
- `models/dice.py`: colonist balanced dice (exact port; tested against a reference).
- Extension: `bot/` (bot), `recorder/` (capture-only recorder). Load unpacked in Chrome.

Useful commands:
```bash
.venv/bin/python -m catanatron.colonist.evaluate A B --games 200 --workers 8    # e.g. Z:runs/sp1/best.npz:100 AB:2
.venv/bin/python -m catanatron.colonist.convert captures/X.json -o /tmp/rec && .venv/bin/python -m catanatron.colonist.replay "/tmp/rec/*.json"
.venv/bin/python -m catanatron.colonist.bridge runs/sp1/best.npz --play --simulations 800 > bridge.log 2>&1 &
.venv/bin/python bench/selfplay.py models/ab2_40k/net.npz --simulations 50 --games 2
```

## 4. Status by phase

- **Phase 0 (simulator = colonist):** done. A full real game replays with all checks.
- **Phase 1 (evaluation harness):** done. Ladder: W vs W 48.4±3.1, W vs R 61.9±3.0,
  F vs W 99.7, AB:2 vs F 53.7±2.8. Seeded games repeat exactly.
- **Phase 2 (learned net):** done. `models/ab2_40k/net.npz` trained on 40,000
  AlphaBeta-vs-AlphaBeta games (`data/ab2`, 800 shards; 330 of them made on an old
  Azure VM under the pre-fix friendly-robber/longest-road rules). Policy 80.9%
  agreement, value loss 0.59 (no overfitting). vs AlphaBeta: **73.5% ± 6.1 at 200
  sims, 83.0% ± 5.6 at 800 sims** (200 games). Earlier 2k-game net was 6%.
- **Phase 3 (self-play):** running. Run dir `runs/sp1`
  (`--init models/ab2_40k/net.npz --simulations 100 --reference AB:2,F --extra-data data/ab2`).
  - Round 1 (on the Mac, 1h36m): **promoted**, beat previous best 72% (100 games);
    **benchmark vs AlphaBeta 78.8% ± 4.3 at 100 sims** (≈ equal thinking time). The
    plan's Phase 3 gate (≥65% vs AB) is met at equal-ish time.
  - Rounds 2–10 running on a Google Cloud VM (section 6). Round 2 started 20:16 UTC
    (VM clock) on 6 Oct; expect ~2–2.5 h per round.
  - The Mac's `runs/sp1` stops at round 1; **the VM copy is now authoritative.**
- **Phase 4 (live bot):** working end to end. 3 full games vs colonist computer
  opponents and 2 vs people finished without stops after fixes; first win vs an Easy
  computer opponent. All move formats verified live except Road Building played by
  the bot. The bridge currently running on the Mac: `runs/sp1/best.npz --play
  --simulations 800`.

## 5. The open problem: road-heavy, sheep-less play

Owner's observation (live, also at 800 sims): the bot builds roads relentlessly, once
all 15 before a settlement. Analysis of that game (`captures/bridge-20261006-162624.json`):
- Its starting spots had **no wool** (brick/ore/wood + grain/brick/wood); settlements
  need wool, so roads were the only affordable build. Each time it got wool it settled
  immediately.
- The real mistakes: **wool-less opening**, and **4:1 trading ore for wood (more roads)
  instead of for wool** while 5–11 settlement spots were open.
- In simulation vs AlphaBeta the 40k net builds normally (≈11.7 roads, 5.3
  settlements, 4.3/5 starting resources; AlphaBeta 12.2 / 5.8 / 5.0). So live play
  (people / colonist bots) exposes it more, and play-time search doesn't fix it (the
  net's judgment is the issue, not search depth).
- Another observed mistake (40k net, win vs Easy bot): ended turn holding exactly a
  city (search split 42% end turn / 42% + 17% city at two spots).

**Measurement (implemented locally 6 Oct; not yet uploaded to the VM):**
`python -m catanatron.colonist.diagnostics --a Z:runs/sp1/best.npz:100 --b AB:2
--games 200 --workers 4` measures paired simulated games; set `--b` equal to `--a`
for self-play. `--captures 'captures/*.json'` converts and verifies live captures,
deduplicates repeated saves of one game, and reports the recording account separately.
`--json PATH` saves the aggregate report (no usernames or chat). It counts opening
resource gaps, road and wood-trade decisions while exactly one settlement card is
missing and a site is open, four ore for wood while sheep is missing, and turns ended
with a legal city or settlement. Each event includes the win rate in affected games.
`loop.py` now saves `round_NNN/benchmark-diagnostics.json` when its benchmark runs;
the running VM still has the older code and will not emit this file until updated.

First local 200-game sample at 100 simulations: the net beat AB:2 in 155 games
(77.5% ± 5.6%); it opened without sheep production in 103/200 games, vs 1/200
for AB:2. Its win rate was 71.8% in those 103 games, versus 83.5% in the other
97 (association, not a controlled effect). Across 77 live capture files, 16
distinct traces replayed cleanly (9 reached a winner); 56 files were duplicate
saves and 5 could not be verified. The recording account opened without sheep
production in 11 of those 16 and made 5 four-ore-for-wood trades while holding
no sheep and having an open settlement site.
In a separate 200-game net-vs-itself paired sample, the two seats together
opened without sheep production 164/400 times (41%), built 1,558 roads while
one settlement card short, and ended a turn with a legal city 20 times.
Restricting live files to bridge captures gives 13 distinct verified traces,
8 with a winner; the bot account missed sheep production in 11/13.

**Setup exploration (implemented locally 6 Oct; not yet uploaded to the VM):**
`selfplay.py` and `loop.py` accept `--setup-noise-scale` (default 1). A value such
as 2 scales Gumbel noise only for initial settlement choices in self-play;
initial roads, later moves, evaluation, and live play retain their defaults.
Each data folder stores `setup-exploration.json` and refuses to resume shards
at a different scale. Use a new run directory for a controlled trial. On 100
boards at 10 simulations for setup only, scale 2 changed 129/200 seat openings
and reduced sheep-free openings from 82/200 to 73/200. This checks that the
flag affects choices; it says nothing yet about trained playing strength.

**One-round local trial started 6 Oct:** `runs/setup_40k_noise2` starts from
`models/ab2_40k/net.npz`, with 5,000 self-play games, 100 simulations,
`--setup-noise-scale 2`, two workers, opponents `AB:2,F`, and `data/ab2` added
to training. The exact settings are in `runs/setup_40k_noise2/run-config.json`.
The first two shards (128 games) completed at about 0.52 games/s with 43%
system memory free. It runs under `caffeinate -i` in a Codex terminal session.
If interrupted, resume with the same flags and run directory, omitting `--init`.
The original cloud trainer was left running. A separate 4-core VM was created
for this trial but deleted after automatic approval review rejected copying
the code, simulated data, and model to it; no payload reached that VM.

**Proposed next steps:**
1. Train a trial from the same checkpoint with setup scale 2, then compare it
   against the unchanged checkpoint and AlphaBeta on paired boards.
2. Only if needed: 200 self-play sims; a settlement-first reference bot as an opponent;
   last resort, a hand rule (opening must reach all 5 resources or a port).
The VM loop can be stopped and restarted with new flags at any time; it resumes.

## 6. Running infrastructure (check before changing anything)

**Extra bot accounts, from 10 Oct 2026:**
- **Queues:** a new account plays **casual** until it has 5 verified casual games
  (`--ranked-after 5`: colonist's home page shows "0/5 Casual games to unlock"),
  then
  **ranked**, the owner's choice.
  - If ranked still isn't available, it plays casual and retries.
  - Games against our own bots are played out (no auto-resign, the owner's choice).
  - The owner was advised to tell colonist about running several bot accounts in
    ranked.
- **Services:** each runs as `colonist-casual@<account>` on the bot VM, with
  `scripts/vm/bot/accounts/<account>.env` setting a unique `PORT` (8766, 8767,
  ...). The VM fits 2-3 alongside the ranked bot.
- **Our own accounts:** `scripts/vm/bot/own_accounts.env` lists our usernames.
  A game against one is logged `own_bot` and kept out of the training data.
- **Sign-in, once per account:** `scripts/cloud_ranked_login.sh <account>` on the
  bot VM, through an SSH tunnel to port 6080 (noVNC). The owner signs in;
  never type passwords for them.

**Bot VM 2** (AWS `c7i-flex.large` 3.18.220.56, tag Role=bot) has run since
11 Oct: **bot3** (Dilly6283, port 8766) and **bot4** (Sherie3424, port 8767).
`scripts/vm/bot.env` on the coordinator lists both bot VMs, and the sync,
`bot_ctl.sh` and the watchdog cover both. The new accounts' game sessions
reconnect often (`isReconnectingSession`, sometimes on the same socket); the
bridge resends or catches up a move lost to any reconnect.

**bot2** (colonist username Ericha8499) has run since 11 Oct, about midnight
Toronto, as `colonist-casual@bot2` on port 8766.
- **Ranked unlock:** the runner reads colonist's "N/5 Casual games to unlock"
  counter from the home page and switches to ranked when it disappears.
- **Reconnects:** bot2's browser reconnected about once a minute in its first
  games (the ranked bot about once per 80 games). Likely cause: the account open
  in another session. The bridge now recovers from a reconnect whatever the
  unconfirmed move was.
- **Watchdog:** runs every 15 minutes and covers every account
  (`bot_ctl.sh accounts`, `restart-account NAME`). The bot VM is full with two
bots: about 0.7 GB of memory free, CPU load about 2.6. A third account needs
another VM; the owner chose to hold off.

**Since 9 Oct 2026, 10:05 pm Toronto: the ranked bot has its own VM.**
- **Host:** the AWS `c7i-flex.large` at 18.226.232.112 (user `ubuntu`, services in
  `scripts/vm/bot/`, host in `scripts/vm/bot.env` on the coordinator).
- **Rendering:** a 960x540 window at 5 frames a second (`--viewport`,
  `--max-fps`), so Chromium's software drawing takes about 40% of one core
  instead of 3.5 cores. Moves take about 0.7-1 s.
- **Sync:** `sync_bot.sh` (coordinator cron, every 5 minutes) sends code and
  runs/g1's nets out and brings `data/ranked` and `captures/ranked` back.
- **Control:** `bot_ctl.sh status|log|restart|stop` manages it from the
  coordinator, and the watchdog uses it.
- **The coordinator** no longer runs the ranked or spectator services. Training
  uses all 8 cores (8 self-play processes, 8 training threads).

**Since 9 Oct 2026, 6:25 pm Toronto: the graph net.**
- Training runs `runs/g1` (`scripts/vm/train.sh`, log `runs/train.log`), from
  `models/graph1`: the board-aware graph net in `graph.py`, trained from scratch on
  sp1's rounds 9-14.
- Results before the switch:
  - 57.4% against sp1 round 15 (500 games) and 60.3% against round 16 (300 games).
  - AlphaBeta benchmark 91.0%; live value MSE 0.568, against round 15's 0.580.
- The ranked bot follows `runs/g1`'s promotions.
- `runs/sp1` (the fully connected net, best round 17) is stopped and kept as it
  was; `scripts/vm/train-sp1.sh` restarts it.
- Live play at 800 simulations takes about 1-6 s per move while self-play shares
  the coordinator.

**24/7 setup (since 8 Oct 2026), on `colonist-selfplay-1`:** `~/colonist-ai` is now a
git checkout of `main-9mnmbh`. It pulls and pushes with a read-write deploy key,
`~/.ssh/github_deploy`. Three systemd services start at boot, defined in `scripts/vm/`:
- `colonist-ranked`: `ranked_runner --games 0 --promotions runs/sp1 --simulations 800`.
  - It plays the promoted net and switches nets between games.
  - Each verified game goes in `data/ranked/games.jsonl`, with the net's hash and
    round. Earlier games were backfilled from the runner logs.
  - Captures carry a `bot` field with the same information. Shards in
    `data/ranked/{bot_value,human_openings}` carry `net`.
- `colonist-train`: `scripts/vm/train.sh`, runs/sp1 from round 11, with playout cap
  randomization: 25% of moves get 160 simulations, the rest 24.
  - Since round 15:
    - The loose promotion gate (`--gate loose`): a net is promoted unless it's
      clearly worse, if its guardrails hold.
    - Training on the last 8 rounds, drawing 4M positions per epoch
      (`--window 8 --train-sample 4000000`). The compact loader in `train.py`
      peaks at about 9.75 GB.
    - Matches and benchmarks are spread over the workers as well
      (`--distribute-eval`).
  - Self-play is also spread over the workers in `scripts/vm/workers.env`
    (`user@host:processes`). The loop reads that file again every round and copies
    its code to every worker first.
  - Workers since round 12:
    - GCP `colonist-selfplay-worker-4` (4 cores).
    - Azure `colonist-az-worker-1` (`Standard_D4as_v4`, 9.160.33.122).
    - Azure `colonist-az-worker-2` (`Standard_D2s_v4`, 9.160.34.57).
  - The Azure VMs are in resource group `colonist-ai-workers`, belgiumcentral,
    paid from the Azure for Students credit. Their firewall allows SSH only from the
    coordinator's static IP 34.60.203.129 (`colonist-coordinator-ip`) and the
    owner's Mac.
  - Students quota is 6 vCPUs per region. The other allowed regions only offer
    burstable B-series sizes.
  - Measured self-play speed per Azure vCPU is about the same as per GCP T2A core.
  - Since 2026-10-09 there are also four AWS workers:
    - `c7i-flex.large` (2 vCPU each), us-east-2, on-demand, user `ubuntu`, in the
      owner's AWS project account 609022620573.
    - The new-experience free plan only allows free-tier-eligible types, and the
      organization blocks spot.
    - `/home/owen/colonist-ai` links to `/home/ubuntu/colonist-ai` there, so the
      worker root is the same as on the other workers.
    - Security group `colonist-workers`: SSH from the coordinator and the owner's
      Mac.
    - `scripts/vm/aws_workers.sh status|start|stop` manages them from the Mac
      (`aws login --profile colonist`).
    - About 49.6 s per 8-game shard per process, the fastest per vCPU of all the
      workers.
  - Every round's net is scored on the live games (`calibrate.py`). A promoted net
    also plays a gauntlet against rounds 2 and 5.
- `colonist-spectator`: the public feed, as before.
- Watchdog: `scripts/vm/watchdog.sh` runs from cron each hour.
  - It does a cheap health check, and only on a problem starts Claude Code with
    `watchdog.md` and a fixed tool allowlist.
  - Its notes go to `data/watchdog/{health.log,journal.md}`. A
    `data/ranked/NEEDS_OWNER.md` means it stopped and needs the owner.
- Status:
  ```bash
  systemctl status colonist-ranked colonist-train colonist-spectator
  tail data/ranked/bridge.log runs/sp1.log data/watchdog/health.log
  ```
- Update code: `git pull`, then `sudo systemctl restart colonist-train`. Restart
  `colonist-ranked` only between games.
- The old upload-based folders (`~/colonist-ai-old`, `~/colonist-ai-ranked`) are kept
  for now and can be deleted.

The notes below are from before the 24/7 setup.

**Google Cloud VM (running rounds 2–10):**
- Project `colonist-ai-selfplay` (billing: the $300 / 90-day free trial, "My Billing
  Account" 01212C-E56C4C-120FB3), zone `us-central1-a`, instance `colonist-selfplay-1`,
  `t2a-standard-8` (8 ARM cores, 32 GB), Ubuntu 24.04, ≈$0.30/h. Trial quota: 12 vCPUs
  total, no spot.
- Code at `~/colonist-ai` on the VM (uploaded with `git archive`, not a git checkout;
  re-upload to update code). Same `.venv` via `scripts/setup.sh` (CPU torch).
- The loop runs detached (`setsid nohup`), log `~/colonist-ai/runs/sp1.log`.
- **Auto-shutdown watcher**: a detached bash loop on the VM shuts the VM down ~1 min
  after `colonist.loop runs/sp1` exits (end or error). Data stays on the disk.
- Commands (from the Mac; `gcloud` is installed and logged in as the owner):
  ```bash
  gcloud compute ssh colonist-selfplay-1 --zone us-central1-a --command "tail -n 5 ~/colonist-ai/runs/sp1.log"
  gcloud compute ssh colonist-selfplay-1 --zone us-central1-a --command "cat ~/colonist-ai/runs/sp1/log.csv"
  gcloud compute scp colonist-selfplay-1:~/colonist-ai/runs/sp1/best.npz models/sp1_best.npz --zone us-central1-a
  gcloud compute instances stop colonist-selfplay-1 --zone us-central1-a      # or start
  ```
- Restarting the loop on the VM with other flags: kill it, then
  `cd ~/colonist-ai && setsid nohup .venv/bin/python -m catanatron.colonist.loop runs/sp1 --rounds 10 --simulations 100 --reference AB:2,F --extra-data data/ab2 > runs/sp1.log 2>&1 < /dev/null &`
  (flags aren't stored in `state.json`; pass the same ones unless changing them on
  purpose). Re-add the auto-shutdown watcher if you restart.

**Azure (mostly retired):** Azure for Students subscription. The old datagen VM was
deleted; its OS disk `colonist-datagen-1005-1054_disk1_...` remains in resource group
`COLONIST-AI-VMS` (canadacentral), costing a little; delete it if the owner agrees.
The student subscription can't get quota increases and only offers burstable
`B4ps_v2`/`B4pls_v2` (40% baseline), so it isn't useful for training.

**Mac:** the bridge is running (see above). Self-play on the Mac takes ~1.5–2 h per
round at 100 sims, but **memory is the bottleneck**: with Cursor/Chrome open, swap
filled to 11 GB and slowed everything 4×. Close Cursor/Chrome/Docker before long runs,
and use `sudo pmset -a disablesleep 1` (owner runs it) to keep it awake with the lid
closed, then `... 0` after. Spotlight indexing caused a load-average spike once.

## 7. Colonist protocol notes (learned live)

- Client game message: header bytes `3,1`, channel (room `serverId`), MessagePack
  `{action, payload, sequence}`. Formats in `client.py`.
- Roads/settlements: setup sends bare `11 edge` / `15 corner`; **after setup the client
  first sends `10` (road) / `14` (settlement)**; Road Building's free roads are
  `11 edge` then `21 edge`. City: `17` then `18 corner`.
- Discards: `8 [cards so far]` after each pick, then `7 [all]` (no card played first).
- Decline trade: `50 {id, response: 1}`.
- Monopoly / Year of Plenty: `48 card`, then `8`s, then `7`.
- Rules found live (fixed in the simulator): the friendly robber also protects the
  **mover** at ≤2 visible points; longest road counts the road **into** an opponent's
  settlement but not through it.
- Log entries skipped as notices: 0, 2, 22, 24, 44, 47, 49, 58 (timer moving the robber),
  60, 64 (timer choosing discards), 66, 68, 118 (trade offer), 130, 139 (robber victim
  count). Monopoly/YoP cards may arrive in a later update; colonist's Easy bot can play
  Monopoly and never choose (recorded as taking nothing).
- The bridge refuses games joined midway (a mid-game snapshot after a reload) and
  never plays on a position it can't verify.

## 8. Known small issues / ideas

- The bridge re-saves a finished game on every extension reconnect (many duplicate
  `captures/bridge-*.json` files); harmless, could dedupe.
- The bot only declines trades; it never proposes them (by design).
- `train.py` picks `net.npz` by combined policy+value validation loss; fine now that the
  value head doesn't overfit with 40k games.
- Self-play is slower on the Mac than the 1-game benchmark suggests: efficiency cores,
  fanless throttling, and memory pressure.
