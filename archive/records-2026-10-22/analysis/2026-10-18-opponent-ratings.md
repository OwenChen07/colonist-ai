# Win rate by opponent rating (Oct 18, 8:43 PM Toronto)

Source: colonist's game-over message lists both players' rating before the game,
so these are ratings at game time. No API calls were needed. Ranked games on
bot3/4/5/7, own-bot games left out. Script:
`data/orchestrator/scratch/live-loss/opp_rating.py N`; cache (ratings only, no
names): `data/orchestrator/analysis/opponent_ratings.json`.

## Last 400 games (Oct 17, 11:27 PM to Oct 18, 8:33 PM), 398 with ratings

| Opponent rating | Games | Bot win rate [95% CI] | Elo edge | Opp mean | Bot mean |
|---|---|---|---|---|---|
| under 2100 | 215 | 60.5% [53.8, 66.8] | +74 | 2006 | 2285 |
| 2100-2300 | 157 | 58.0% [50.1, 65.4] | +56 | 2174 | 2278 |
| 2300-2500 | 13 | 69.2% [42.4, 87.3] | +141 | 2395 | 2279 |
| over 2500 | 13 | 38.5% [17.7, 64.5] | -82 | 2572 | 2258 |
| all | 398 | 59.0% [54.1, 63.8] | +64 | 2103 | 2281 |

## Last 1200 games (Oct 16, 6:17 AM to Oct 18, 8:33 PM), 1194 with ratings

| Opponent rating | Games | Bot win rate [95% CI] | Elo edge | Opp mean |
|---|---|---|---|---|
| under 2100 | 595 | 63.2% [59.2, 67.0] | +94 | 2015 |
| 2100-2300 | 535 | 55.0% [50.7, 59.1] | +35 | 2172 |
| 2300-2500 | 42 | 54.8% [39.9, 68.8] | +33 | 2377 |
| over 2500 | 22 | 36.4% [19.7, 57.0] | -97 | 2568 |
| all | 1194 | 58.7% [55.9, 61.5] | +61 | 2108 |

## What the rating system asks for (last 400)

A win pays about +12, a loss costs about -18. The bot is usually 180 points above
its opponent, so it needs about 60% just to stay level.

| Opponent minus bot | Games | Win pays | Loss costs | Break-even | Actual |
|---|---|---|---|---|---|
| under -300 | 88 | +11.3 | -18.7 | 62.2% | 65.9% [55.5, 75.0] |
| -300 to -200 | 107 | +11.5 | -18.5 | 61.7% | 54.2% [44.8, 63.3] |
| -200 to -100 | 87 | +12.1 | -17.7 | 59.4% | 63.2% [52.7, 72.6] |
| -100 to 0 | 63 | +13.1 | -16.8 | 56.2% | 58.7% [46.4, 70.0] |
| above 0 | 53 | +13.4 | -16.8 | 55.6% | 50.9% [37.9, 63.9] |

## Reading

- The bot's strength is about 2170 against the people it meets (opponents'
  average 2108 plus +61). Its accounts sit at about 2230-2280 because the system
  pays more for wins than the opponent gap would suggest; it is at its level.
- Win rate does fall with opponent strength: 63% under 2100 vs 55% at 2100-2300
  (1194 games; the intervals barely touch).
- Above 2300 there are only 64 games in 1200 (5%). The bot won 31 of them,
  48% [36, 61], against an average of about 2440: a performance of about 2430.
  Against 2500+ alone it wins 36% [20, 57] (22 games).
- Rank 1 (2650) means holding 62% or more against opponents 400+ points lower,
  and about half against the few 2500+ players. Against strong players the bot
  plays like a 2430; that is roughly 200 points short, with a wide margin
  because it rests on 64 games.
