# Does more thinking build more cities? (Oct 19, 4:35 PM Toronto)

Live-play task 4, cities (live-loss: people beat the bot by out-building it, 4 cities in 54% of their wins against 47% of ours).
Offline, net runs/g1/best.npz (round 73), human-game captures (data/human/index.jsonl, newest first), 2-player games.
Position = our turn, a city is affordable and some other build or buy is too. Early = nobody at 6+ visible points; late = someone is. The first of each per game, 60 of each.
Each position: two searches at today's turn budget (4,800 simulations, different seeds) and one at E61's (12,000).

| | early | late |
|---|---|---|
| live game picked the city | 43/60 | 37/60 |
| 4,800 search picks the city (two seeds) | 39, 40 | 43, 41 |
| 12,000 search picks the city | 39 | 42 |
| two 4,800 searches disagree | 6/60 | 7/60 |
| 4,800 vs 12,000 disagree (mean of both) | 10.5/60 | 12/60 |
| mean time, 4,800 / 12,000 | 5.1 s / 12.9 s | 4.7 s / 12.0 s |

Reading: deeper search builds a city exactly as often as today's search (early 39 against 39.5, late 42 against 42). It changes some moves (4 to 5 points beyond the noise, like the road-race audit) but not toward cities. When the bot skips an affordable city, that is the net's judgment, not a lack of thinking time. So no search-side lever here: no city-specific budget test. Better city tempo would have to come from training (the opponent model or the targets), so this goes to training/research as a finding, not a live experiment.

Script: data/orchestrator/scratch/live-play/city_depth.py (NET OUT per_class base deep workers last), 3 workers at one thread each, about 14 min.
