# Workstream: late-game

**Aim:** a second research worker, on one question only: why the bot loses games it rated 60-85% won against strong people, and what change fixes that within days, not weeks. The season on colonist.io ends about Oct 31, 2026 and the owner is pushing for rank 1 (about 2650 Elo); the leading account plays E54's step 50. 86% of our losses to 2300+ players come from a 60%+ peak (live-loss, Oct 22); the bot's own chair is rated about 3 points too sure from the start position (E84); the slide happens while the opponent buys development cards and builds cities, with no single bad move in the search's trace. Created Oct 22, 2026 from the closed OpenHex workstream's budget (owner's decision, 6:54 PM Toronto).

**Owns:** the late-game line of experiments in `experiments.md` (E84, E88, E89 and what follows), `data/orchestrator/analysis/late-*.md`, and offline evaluation of candidate fixes: value fine-tunes on people's late positions, the people model in the opponent's chair, search changes in the late game (think-time split, a KL to the people model, opponent-aware robber and card play). The `research` workstream owns the net's architecture and the training recipe; `live-loss` owns the live loss data and its daily metric; `live-play` owns the accounts. You use their tools and data; you don't change their files without a message.

**Rules for this workstream:**
- Offline only. Nothing you do changes production or the live bots. A result that deserves live play is written up (numbers, ± intervals, the account you want it on, the harm stop) and goes to the lead, who decides; from Oct 29 nothing new goes on the top three accounts.
- The PC's GPU is shared with `research` and production self-play: ask `research` before any GPU job, hold `gpu.lock` while it runs, remove it after. Fleet matches only when the lead has said there is room; say how many games and how long.
- Every experiment is an E-number in `experiments.md` and `experiments.json` before it runs: hypothesis, metric, games until a verdict, cost. Every result rests on a count and an interval; 1,000 games are ±3%.
- Measure against people first (the 2100+ and 2300+ holdouts, paired by game), self-play second. A change that helps self-play and not people is not a result.
- Land code through `land.sh` with tests; nets must keep loading.

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
