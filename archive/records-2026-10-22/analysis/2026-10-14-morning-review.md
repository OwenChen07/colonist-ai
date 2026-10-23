# Oct 14 morning review

Context snapshot: jobs/context-20261014-morning.md (09:11 Toronto).

## E2: exact-time controls

Ranked games with winner, excluding own_bot, from Petra's first model game
at epoch 1791857590 through latest completed model game 1791983223:
- Petra: 93/175, 53.14%, Wilson 95% 45.8–60.4%.
- Sherie: 84/151, 55.63%; Dilly: 90/176, 51.14%.
- Pooled controls: 174/327, 53.21%, Wilson 95% 47.8–58.6%.
- Difference: -0.07 percentage points, approximate independent-binomial
  95% margin ±9.16 points. Repeated opponents, different account ratings,
  model refreshes and non-random assignment weaken causal interpretation.

Extend unchanged to 400 model games; no rollout or mix change.

## E5: same-holdout calibration

Ran existing calibrate module for round_031 and round_039 against
data/human/holdout/value. Both saw 12,930 positions from 329 games:

| Net | Overall MSE | Opening | Middle | Late | Accuracy |
| --- | --- | --- | --- | --- | --- |
| 31 | 0.6149 | 0.9195 | 0.5475 | 0.4703 | 0.7624 |
| 39 | 0.6128 | 0.9203 | 0.5462 | 0.4640 | 0.7630 |

Overall delta -0.0021; no game-cluster confidence interval available from
this aggregate CLI, so no significance claim. Opening delta +0.0008.
All eight rounds 32–39 failed to promote. Extend E5 through round 43:
the six-round replay window still mixed earlier 25% rounds initially.
Do not treat pooled matches against the same incumbent as independent proof.

## E6: runtime audit

Round 31 launch 23:43:03Z to finish 01:09:35Z: 86m32s.
Round 32 launch 02:10:23Z to finish 02:56:43Z: 46m20s (46.5% shorter).
Rounds 37–39: about 45m48s, 46m51s, 46m18s including dispatch.
Each completion log reports 3500 games across 8 machines.
Host ranges in round 32 are consecutive and disjoint: offsets
0/896/1408/1920/2176/2432/2688/2944, counts
896/512/512/256/256/256/256/556, total 3500.
Keep scheduling provisionally. Realized human-start fraction, individual
NPZ seed uniqueness and per-host completion times remain unaudited;
logs alone cannot prove those metadata checks. E5 changed concurrently,
so throughput attribution is operational evidence, not a controlled trial.

## Research: extra search in contested road positions

New evidence: road-benchmark-pilot.json and road-benchmark-uncapped-pilot.json;
implementation: catanatron/catanatron/colonist/road_benchmark.py.
Both pilots examine only 10 positions from 10 captures (21 scanned).
800 versus 1600 requested simulations changed one decision in each pilot.
At a 2s cap, actual means were 436 versus 671 simulations, 1.70 versus
2.04 seconds. Uncapped, actual means were 800 versus 1600, 2.95 versus
6.10 seconds. This tests neither win rate nor an equal-time allocation.
Agreement with recorded play and the deeper search's own value are not
strength metrics. Also Search here has no opponent-model argument: offline
results would not directly validate Petra's model-enabled search.

Concrete next test after E2: broaden to >=100 contested training-split
positions with a frozen net, paired RNG and recorded elapsed time, then
1000 seat-swapped games against incumbent at matched aggregate compute.
Require win-rate evidence and anchor/live calibration guardrails before a
one-account >=400-game ranked experiment. Wait now: tiny pilot, no outcome
labels, scarce coordinator compute during self-play, and occupied live slot.
