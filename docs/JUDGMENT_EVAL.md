# Judgment improvement experiment

This experiment targets tactically wasteful decisions such as spending the
last road when Longest Road is already secure and the placement opens no
settlement site. It never labels a road as a bad move solely because the bot
lost the game. Largest Army ties are audited with the incumbent rule; knight
plays are not filtered without a separately verified failure case.

## What is implemented

1. `bonus_tactics.py` calculates public, exact one-piece road and army-race
   facts. The road flag is deliberately narrow: an opponent with at most one
   road and one settlement piece cannot take the bonus in either order, the
   move uses our last road, opens no settlement site, blocks no currently
   buildable opponent edge, and does not bring our hand below the discard
   limit. Other long-term effects remain possible.
2. `bonus_audit.py` replays a complete private capture before counting any
   decision. Its output has aggregate counts only.
3. `judgment_policy.py` is an isolated candidate. It removes flagged roads
   from the **root** legal moves; all later search nodes retain complete legal
   actions. The comparison player uses the same network, search budget, and
   deterministic root choice as the live bridge.
4. `judgment_benchmark.py` compares the two on replayed positions with shared
   hidden-deal seeds and on paired simulated boards with seats swapped. It
   prints aggregate counts and hashes of the frozen inputs. It never deploys.
5. The live bridge has a **default-off** `--judgment-filter` flag for an
   eventual controlled rollout. Captures made with it record that fact in
   their bot metadata. Leave it off until the review gate passes.

## Evaluation protocol

Keep `data/human/holdout` separate from tuning. Freeze a model snapshot and
the verified capture index before the run. The command detects changes to
either file while it is running. Use a separate training-split run for
iteration and a holdout run for the final check. A direct `--capture` run is
only a smoke test and can never pass the review gate. Its output says whether
the supplied network matches the model fingerprint stored with that capture.

Example, from the repository root, once competing training work has finished:

```sh
.venv/bin/python -m catanatron.colonist.judgment_benchmark \
  models/frozen-candidate.npz --index data/human/index.jsonl \
  --captures captures --split holdout --last 1000 \
  --max-positions 60 --per-game 2 --repeats 4 \
  --simulations 4800 --time-limit 5 \
  --games 1000 --paired-simulations 200 --seed 50300 --workers 4
```

The single-capture decision comparison uses live-style deterministic root
search and reports the rate of flagged choices across shared hidden-deal
seeds. Agreement with a recorded move is descriptive, never an optimality
label. The paired match measures actual simulated wins and thinking time.
The match has no live clock; its lower simulation cap is equal for both
players. The direct decision comparison uses the five-second live budget to
measure time cost. The paired match at 200 simulations is a strength screen,
not a claim about the exact live 4,800-simulation win rate.

The automatic review gate requires all of the following:

- At least 30 held-out decision positions and at least one flagged baseline
  choice, so the candidate has a measurable opportunity to improve.
- No flagged candidate choices in the sampled positions.
- At least 1,000 seat-swapped games and a 95% lower score bound of 48%.
- Candidate thinking time no more than 105% of baseline time at the five-second
  live decision budget.
- The model and index remain unchanged throughout the run.

Passing means **eligible for promotion review**, not automatic deployment.
An inconclusive result calls for more independent games or positions. If the
candidate passes, run a small controlled live rollout and compare automatic
capture-audit rates, completion, timeouts, and ranked results with the
baseline before expanding it. Do not interrupt active games when switching.
The rollout uses the bridge flag only between completed games.

Raw captures, usernames, chat, hands, tokens, and private paths stay out of
the report. No one needs to verify games by hand: conversion and replay are
the admission gate, and the paired match supplies the strength comparison.
