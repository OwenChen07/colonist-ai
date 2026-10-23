# Workstream: efficiency

**Aim:** more self-play games, and stronger searches, from the same compute. Training data per hour is the training loop's bottleneck on the free-tier VMs.

**Owns:** `selfplay.py`, `mcts.py` (speed, not search policy), `distributed.py`, `encode.py`, `graph.py`/`net.py` inference speed, Catanatron's game engine hot paths, and `bench/`.

**Priorities (lead, Oct 18, 2:50 PM):**
1. **The compiled engine trial (owner approved Oct 18, 9:01 AM, decision compile-engine).** The owner said yes to a trial: install Cython in your own worktree only, compile the engine's core files with the source unchanged, and measure games per hour per process on the coordinator with the production self-play settings. Nothing goes on the VMs and nothing lands until the lead has the numbers. Prove play unchanged with identical shards on fixed seeds (your merge_cmp method). Report: before → after seconds per game, which files compiled, the build time, and what a VM rollout would need (packages, build step, fallback to pure Python if the build fails). The lead takes the rollout decision to the owner.
2. **E36's verdict** from rounds 96-98 (self-play minutes per round, the tail, worker memory), as written in the ledger.
3. The queue and the fleet keep working while you do this: a round that waits on self-play, a host that drops out, or a stop that hangs comes first.

**Rules for this workstream:**
- A speedup must leave play unchanged. Show it with identical moves on fixed seeds, or with a match of at least 400 games inside the noise, before landing.
- Report every gain as before → after, in games per hour per core.

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
