# Workstream: research

**Aim:** find the change to the net or its training that breaks the plateau. You work offline, on the gaming PC's GPU, and hand what works to the `training` workstream, which runs the production loop. The goal of the project is colonist.io rank 1 (about 2650 Elo); what matters is strength against people, measured live.

**Owns:** the net's architecture (`catanatron/colonist/graph.py`, `net.py`), the training targets and loss (`train.py`'s value/policy/aux targets, the value mix, data weighting), the learning-rate and EMA schedule, and every offline training experiment. Your GPU: the owner's PC, `owen@100.125.136.114` (ssh with the worker key; respect `gpu.lock` there: hold it while you train, remove it after). Production trains on the cloud VM, so the PC is yours, but keep its self-play workers as configured and never reboot it.

**Rules for this workstream:**
- Offline only. Nothing you do changes production: no edits to `scripts/vm/train.sh`, `workers.env`, the loop's flags, or the live bots. When a result deserves production, write it up (numbers, ± intervals, cost per round) and message `training` and the lead; the lead decides.
- One GPU job at a time on the PC; `gpu.lock` while it runs. Fleet matches (evaluate on the self-play hosts) only when the lead has said the fleet has room; say how many games and how long.
- Every experiment is an E-number in `experiments.md` and `experiments.json` before it runs: hypothesis, metric, games until a verdict, cost.
- Every result rests on a count and an interval. 1000 games give ±3%; a gate score under 52% over 4000 games is noise against 50%.
- Land code through `land.sh` with tests; new architecture code must keep the old nets loading (the best net is live on four accounts).

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
