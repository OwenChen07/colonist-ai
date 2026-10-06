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
  PATH) and the full `.venv/bin/python -m pytest -q` must pass (currently 497 tests).
- Commit messages end with: `Co-Authored-By: Claude <noreply@anthropic.com>` (use your
  own attribution line if the owner prefers; ask).
- **Never commit `captures/` or `records/`** (real usernames and chat). Never print or
  store colonist's reconnect token. Test fixtures must hold no names or chat.
- The AI **never trades with the other player** (declines every offer). Bank/port
  trades are normal moves.
- Live play: unranked/casual only, from the bot account `CatBot77` that the owner is
  logged into in Chrome. Never type passwords or log in/out. **Don't abandon games**
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

**Proposed next steps (not started; owner hadn't chosen yet):**
1. Measurement script: over a few hundred simulated games (vs AlphaBeta and self),
   count openings missing a resource (no tile and no port), roads/wood trades while one
   resource short of a settlement, ending turn with a city/settlement available, and
   win rate in those games. Also run it on live captures. Then add these as per-round
   diagnostics in `loop.py`'s benchmark.
2. Opening exploration in self-play: more randomness for setup moves in self-play only
   (e.g. larger Gumbel noise / temperature at setup), so it learns the value of openings
   it avoids. Standard AlphaZero remedy.
3. Only if needed: 200 self-play sims; a settlement-first reference bot as an opponent;
   last resort, a hand rule (opening must reach all 5 resources or a port).
The VM loop can be stopped and restarted with new flags at any time; it resumes.

## 6. Running infrastructure (check before changing anything)

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
