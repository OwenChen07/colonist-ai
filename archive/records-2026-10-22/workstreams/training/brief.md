# Workstream: training

**Aim:** a stronger net and search, measured live against people.

**Owns:** `loop.py`, the production training run (`scripts/vm/train.sh`, `workers.env`, the data window and sample), the promotion gate and guardrails, and deployments by `safe_restart_training.sh`. Since Oct 19, 9:18 AM (owner's decision), the search's settings for live play (`BUDGETS`, simulations, the opening, the account env files) belong to the new `live-play` workstream; you no longer change the live bots' play. Since Oct 18, 8:30 PM, the net's architecture, its targets and loss, the learning-rate schedule and all offline training experiments belong to the new `research` workstream (it works on the PC's GPU); you run production. When research hands you a result the lead has approved, you deploy it as an experiment, one at a time.

**Known gaps to work on, in the lead's order:**
1. The live value error is about 0.58, against about 0.48 in self-play at the same search (round 66, measured Oct 17). The opening is NOT where the gap is: its error is 0.89 in both self-play and live games (the result is simply not decided yet). The gap is in the late game (0.43 live against 0.34 self-play) and a little in the middle game (0.51 against 0.49): the net is too sure of a late lead against people. Work on that, not on the opening.
2. Repeat opponents exploit the bot by racing longest road and building cities.
3. Win rate falls to 48% beyond 90 turns.

**The plan for the plateau (lead, Oct 18, 2:55 PM).** Round 73 has held for 20 rounds; rounds 74-93 score 47-50.6% against it. E11b said the net is data-limited, not capacity-limited, and E31 said more passes over the same 6-round window do nothing. The lever left is more distinct data per training, and data that looks like live play. In this order, one production change at a time:
1. **E31's revert watch:** rounds 95-97 at 2 epochs. Late live error must stop climbing (hold at or under about 0.43). If it keeps climbing, set 4 epochs and say so; everything below waits one more round.
2. **E35, colonist's number spiral:** land it now. Its effect builds over the 6-round window; measure live error by phase at rounds 99-101 against rounds 91-93 (0.889 / 0.509 / 0.440). Watch one trap: the anchor guardrail's reference (round 13 vs round 73, 0.597) was measured on random boards. If a net passes the SPRT but fails the anchor by a small margin, re-measure that reference on spiral boards before trusting the fail.
3. **E37, a wider data window:** 20 rounds of equally strong nets sit unused outside the window. Prepare `--window 18 --train-sample 12000000` (train.sh) as an experiment entry with its cost (training minutes per round at 2 epochs on the GPU; a 3.8M-position epoch takes about 190 s, so expect about 20 min, which makes training the round's critical path). The lead decides at the 9 PM run whether it goes to production; the metric is the gate score over 3 rounds against the 47-50.6% run, and a promotion is the signal.
4. **E23 scoring:** the lead launched E23 at 2:45 PM (job e23). When its three arms are in data/orchestrator/e23/, score them as the ledger says and write the verdict.

**Rules for this workstream:**
- Restart training only at a safe point (`safe_restart_training.sh`). One self-play/training experiment at a time, in the ledger first.
- Judge by the gate and guardrails, then by live results over at least 100 games.
- The `live-loss` workstream sends you findings. Weigh them against the evidence.

**Writing your updates (owner's rule, Oct 17, 6:09 PM):**
- Every time in a progress note, inbox message, decision or Discord post is Toronto time, written like "6:09 PM" or "Oct 17, 6:09 PM". Never UTC, never "Z".
- Plain, simple language. The owner reads these on a phone. Say what you did, what changed and what is next in short sentences. Keep function names, flags, pids, IPs and commit hashes out of the prose unless the owner has to act on them; put details like that at the end, in a short "Details" line.
