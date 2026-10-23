# Does more thinking change road-race moves more than other moves? (Oct 19, 4:15 PM Toronto)

Live-play task 4, the longest-road race. Offline, net runs/g1/best.npz (round 73), human-game captures (data/human/index.jsonl, train split, newest first).
Road race = road_benchmark.contested (both longest roads 5+, gap 2 or less, someone at 10+ visible points, our turn).
Control = a late turn (someone at 8+ visible points) that is not a road race. One position of each kind per game, 60 of each.
Each position: two searches at today's turn budget (4,800 simulations, different seeds) and one at E61's (12,000).

| | road race | control |
|---|---|---|
| two 4,800 searches disagree | 7/60 (12%) | 6/60 (10%) |
| 4,800 vs 12,000 disagree (mean of both) | 12.5/60 (21%) | 9/60 (15%) |
| extra change from deeper search | +9 points | +5 points |
| value shift, 4,800 to 12,000 | 0.044 | 0.034 |
| deep picks a road | 17 | 8 |

Reading: deeper search changes a road-race move about 1 time in 11 beyond the noise, and an ordinary late move about 1 time in 20. The 4-point gap rests on 60 positions each (standard error about 7 points), so it is noise for now. There is nothing here that a road-only time boost would do better than E61's boost to every turn, so no separate live test. If E61 wins, a 200-per-class rerun could show whether road races are where its gain comes from.

Script: data/orchestrator/scratch/live-play/road_depth.py (NET OUT per_class base deep workers last), 3 workers at one thread each, 16 min.
