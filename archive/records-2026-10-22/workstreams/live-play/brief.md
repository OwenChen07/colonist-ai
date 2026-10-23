# Workstream: live-play

**Aim:** more live Elo from the net we already have, by changing how the bots play on colonist.io: the search budget and how the clock is spent, the opening, the seat, and the plans people use against us. Owner's decision (Oct 19, 9:18 AM): this is its own workstream next to the training side. The Oct 30 checkpoint: performance against players rated 2300+ from about 2430 toward 2500, and the top account's record from 2405 toward 2450.

**Owns:** the live search settings: `BUDGETS`, `TIME_RESERVE` and `think_budget` in `catanatron/colonist/bridge.py`, `SIMULATIONS` and the per-account env files (`scripts/vm/bot/accounts/<account>.env`, on the bot host, never committed), the opponent model and its mix, the opening in live play, and the play settings of `ranked_runner.py`. Not the net, not the training loop, not self-play (those are `training` and `research`), and not the analysis tools (`live-loss` sends you findings).

**What is already known, so you don't redo it:**
- E18 (Oct 17-18): a doubling of simulations is worth 1-2 points in self-play between 1200 and 4800 (2400 vs 1200: 51.2% ± 3.8% over 600 games; 4800 vs 2400: 52.2% ± 4.1% over 500). The first seat gains more (54.5% and 57.8%). Raw simulations are a small lever; where the time goes may be a bigger one.
- Live budgets today (`BUDGETS`, at 2400 simulations): the first settlement gets 20 s and 20,000 simulations, its road 5 s, the roll 1 s and 800, a turn's actions 5 s and 4,800, the robber and discards the same. Colonist allows 60 s for the opening settlement, 30 s per turn plus 5 s per build, 15 s for the robber.
- Opponent bands (live-loss, Oct 18, last 1,200 games): 63% against under 2100, 55% against 2100-2300, 48% against 2300+ (31 of 64), 36% against 2500+ (8 of 22). At 2350+ a bot must win 59-62% to hold its rating.
- Known exploits (live-loss): repeat opponents race longest road and build cities; the win rate falls to 48% past 90 turns; the net is too sure of a late lead against people (late value error 0.43 live against 0.34 in self-play).
- OpenHex: we lose as the second seat (30-32, against 22-7 first). Nobody has checked the seat split on colonist.

**First tasks, in order; report after each:**
1. Where the clock goes. From the last 200 ranked games per account: per prompt, the time and simulations the bot actually used against what it was allowed, and how often it hit a cap or ran short. One table.
2. From that table, the first experiment: give the decisions that matter more of the clock (a turn's actions get 5 s of a 30 s allowance; the roll gets 1 s). One account first (bot4, the weakest yesterday at 50-52), the other three as control. In the ledger as an E-number with the rule for the verdict: at least 300 games, win rate and rating against the control accounts.
3. The seat split on colonist over the last 1,000 games. If the second seat is weak, look at the second-seat opening.
4. Then the live-loss findings in order of Elo at stake: the longest-road race, cities, games past 90 turns.

**Rules for this workstream:**
- One live experiment at a time across all accounts, in `experiments.md` and `experiments.json` before it goes live, with how to revert it.
- One account first when the evidence is thin; all accounts once a result over 300+ games holds. Restart an account only between games, with `scripts/vm/orchestrator/safe_restart_account.sh <account>`.
- Never abandon a game, never restart mid-game, never touch logins or passwords. Never commit anything from `captures/`, and never quote usernames or chat in Discord posts.
- Judge by live games: 100 give ±10%, 400 give ±5%. A rating move under 100 games is noise.
- Land code through `land.sh` with tests; deploy to the bot host with `scripts/vm/sync_bot.sh`, then a safe restart.

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
