# Workstream: research

**Aim:** find the change to the net or its training that breaks the plateau. Round 73 has held for 32 rounds (74-105 score 47-50.6% against it in the gate). You work offline, on the gaming PC's GPU, and hand what works to the `training` workstream, which runs the production loop. The goal of the project is colonist.io rank 1 (about 2650 Elo); what matters is strength against people, measured live.

**Owns:** the net's architecture (`catanatron/colonist/graph.py`, `net.py`), the training targets and loss (`train.py`'s value/policy/aux targets, the value mix, data weighting), the learning-rate and EMA schedule, and every offline training experiment. Your GPU: the owner's PC, `owen@100.125.136.114` (ssh with the worker key; respect `gpu.lock` there: hold it while you train, remove it after). Production trains on the cloud VM since round 104, so the PC is yours, but keep its self-play workers as configured and never reboot it.

**What is known (read the ledger `data/orchestrator/experiments.md` whole, and `experiments.json`):**
- E11b (Oct 16): three sizes on 3M positions at 12 epochs all overfit; deep (32,64,5) did best on validation. Verdict then: data-limited, not capacity-limited.
- E31: 1 epoch vs 4 made no difference to the gate; extra passes only memorise. Production is at 2 epochs.
- E23 (Oct 18): weighting people's games 16x/32x in the value target did not lower the error on people's positions below production's. Not the lever.
- E35: colonist's real number layout in self-play cut the late live error 0.440 → 0.427. Data that looks like live play helps.
- E39 (train from the best net every round, from round 97): gate 47.6-49.7%, no better than the chain. E37 (18-round window, 12M positions per round) is going live tonight on the VM.
- Live value error 0.58 overall; the gap to self-play is in the late game (0.42-0.43 live vs 0.34 self-play): the net is too sure of a late lead against people.

**First tasks, in order; report after each:**
1. **E40, the deeper net**, as designed in the ledger (deep 32,64,5 vs control, both from round 73, rounds 80-97's data in 3 legs of 4M, 2 epochs a leg, shared seeds). Training's inbox had it; it is yours now. Start it on the PC at once, detached (`data/orchestrator/jobs/e40-run.sh`, pid in `jobs/e40.pid`, log `jobs/e40.log`). Then the E38 gate for each arm against round 73 (4000 games at 200 simulations; 52% passes, 50-52% extends to 8000 at 51%), deep vs control on the same boards, and an equal-think-time match (1000+ games). Report the numbers; adoption is the owner's call because it changes the bots' speed.
2. **Learning rate and EMA for fine-tuning from round 73.** Each production round is round 73 plus 2 epochs at lr 3e-4, EMA 0.9997. Test offline, same data and seeds: lr 1e-4 and 3e-5, with and without EMA, and a short warm-up. Metric: validation policy/value loss and the gate score against round 73 (the lead will give you fleet time for matches; ask).
3. **The late-game target.** Why is the net too sure late against people? Candidates: the value target's mix of game result and search value (E16 tested blends offline; read its result), an auxiliary target for "turns to the end", or training the value head on people's games at turn 60+ only. Propose one with a measurable design before building it.

**Rules for this workstream:**
- Offline only. Nothing you do changes production: no edits to `scripts/vm/train.sh`, `workers.env`, the loop's flags, or the live bots. When a result deserves production, write it up (numbers, ± intervals, cost per round) and message `training` and the lead; the lead decides.
- One GPU job at a time on the PC; `gpu.lock` while it runs. Fleet matches (evaluate on the self-play hosts) only when the lead has said the fleet has room; say how many games and how long.
- Every experiment is an E-number in `experiments.md` and `experiments.json` before it runs: hypothesis, metric, games until a verdict, cost.
- Every result rests on a count and an interval. 1000 games give ±3%; a gate score under 52% over 4000 games is noise against 50%.
- Land code through `land.sh` with tests; new architecture code must keep the old nets loading (round 73 is live on four accounts).

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
