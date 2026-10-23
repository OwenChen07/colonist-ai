# Seat split against people (Oct 19, 1:56 PM Toronto)

Lead's question (Oct 19, 1:55 PM): do we lose as second seat the way we do on
OpenHex (the expansion race: roads and settlements, Longest Road)?

**Data:** the last 1,000 ranked games across bot3, bot4, bot5 and bot7, Oct 17,
9:20 AM to Oct 19, 1:49 PM. All 1,000 converted and replayed cleanly. Only 2 are
after the E58 fix (Oct 19, 1:42 PM), so this is the pre-fix picture: the first
seat's first settlement ran a short search (2,400 sims) in all these games.
No usernames.

## Seat shares and win rates

- The bot moves first in **504 of 1,000 (50.4%, 95% CI 47.3-53.5)**. A coin flip.
- First seat wins **63.3% [59.0, 67.4] (319/504)**; second seat wins
  **54.0% [49.6, 58.4] (268/496)**. Gap 9.3 points. This matches live-play's
  63% / 54%, despite the first seat's weaker first settlement in these games.

## How games end, by seat and result

End-of-game means with 95% bootstrap intervals; proportions with Wilson
intervals. Roads and settlements exclude the two opening ones. "Turns" counts
both players' turns (Catanatron's turn counter). Points are public points
(hidden VP cards excluded).

| measure | 1st seat won (319) | 1st seat lost (185) | 2nd seat won (268) | 2nd seat lost (228) |
|---|---|---|---|---|
| bot roads | 6.68 [6.30, 7.04] | 7.89 [7.48, 8.35] | 7.18 [6.78, 7.56] | 7.89 [7.48, 8.30] |
| person roads | 5.85 [5.45, 6.26] | 9.83 [9.49, 10.17] | 5.82 [5.38, 6.24] | 9.75 [9.46, 10.05] |
| bot minus person roads | +0.82 [0.50, 1.17] | -1.94 [-2.41, -1.46] | +1.36 [1.02, 1.70] | -1.86 [-2.34, -1.39] |
| bot settlements | 3.24 [3.08, 3.41] | 2.83 [2.67, 3.00] | 3.39 [3.22, 3.54] | 2.87 [2.72, 3.03] |
| person settlements | 2.66 [2.50, 2.82] | 4.83 [4.69, 4.95] | 2.59 [2.41, 2.75] | 4.74 [4.62, 4.87] |
| bot minus person settlements | +0.58 [0.43, 0.72] | -1.99 [-2.21, -1.77] | +0.80 [0.64, 0.97] | -1.87 [-2.08, -1.66] |
| bot cities | 2.60 [2.47, 2.74] | 2.04 [1.88, 2.19] | 2.63 [2.49, 2.76] | 1.87 [1.71, 2.01] |
| person cities | 1.43 [1.29, 1.55] | 3.32 [3.22, 3.43] | 1.71 [1.57, 1.85] | 3.37 [3.28, 3.47] |
| bot dev cards bought | 9.00 [8.44, 9.54] | 9.29 [8.73, 9.85] | 8.93 [8.42, 9.48] | 9.36 [8.88, 9.84] |
| person dev cards bought | 4.81 [4.38, 5.22] | 9.78 [9.30, 10.25] | 5.28 [4.81, 5.75] | 10.12 [9.67, 10.56] |
| person holds Longest Road at end | 27.3% [22.7, 32.4] | 82.2% [76.0, 87.0] | 22.0% [17.5, 27.4] | 83.3% [78.0, 87.6] |
| bot holds Longest Road at end | 52.0% [46.6, 57.5] | 17.8% [13.0, 24.0] | 60.4% [54.5, 66.1] | 16.7% [12.4, 22.0] |
| person holds Largest Army at end | 12.9% [9.6, 17.0] | 65.4% [58.3, 71.9] | 12.3% [8.9, 16.8] | 66.2% [59.9, 72.1] |
| bot holds Largest Army at end | 66.1% [60.8, 71.1] | 34.6% [28.1, 41.7] | 68.3% [62.5, 73.6] | 32.5% [26.7, 38.8] |
| game length (turns) | 65.2 [62.6, 67.8] | 82.4 [80.4, 84.4] | 67.7 [65.1, 70.3] | 84.3 [82.6, 86.2] |
| 90+ turns | 13.8% [10.4, 18.0] | 29.7% [23.6, 36.7] | 15.7% [11.8, 20.5] | 36.8% [30.8, 43.3] |
| person resigned or left | 47.3% | 0% | 47.0% | 0% |
| bot ever behind on points | 68.7% | 97.3% | 67.5% | 99.1% |
| first turn the bot was behind | 21.9 [20.0, 23.8] | 23.2 [20.7, 26.2] | 23.8 [21.6, 26.0] | 27.6 [25.2, 30.4] |
| lost games: behind for good from turn | | 57.4 [53.6, 61.2] | | 59.9 [56.8, 63.1] |

## Reading

1. **Losses look the same from either seat.** In both seats' losses the person
   ends about 2 roads and 2 settlements ahead, holds Longest Road in 82-83%, has
   about 3.3 cities and buys about 10 dev cards. Every interval for first-seat
   and second-seat losses overlaps. So against people the expansion race is how
   we lose, **but it is not specific to the second seat**.
2. **The second seat just loses more often** (46% against 37% of games). Its
   losses are a little longer (90+ turns 36.8% against 29.7%, intervals
   overlap) and it falls behind a little later (turn 27.6 against 23.2), not
   earlier. Nothing here points at the second seat's opening going wrong early.
3. **On OpenHex** the opponent took Longest Road in 19 of 21 second-seat losses
   (90%); here 190 of 228 (83%) in second-seat losses and 152 of 185 (82%) in
   first-seat losses. Same pattern, but on colonist it holds for both seats.
4. The bot builds as many roads in losses as in wins (7.9 against 6.7-7.2); the
   person builds far more (9.8 against 5.8). The difference is the person's
   expansion, not the bot stopping.

**One line for training and research:** against people the second seat wins 54%
against 63% first, but second-seat losses look like first-seat losses (person
+2 roads, +2 settlements, Longest Road in 83%), so the seat gap is about how
often, not a seat-specific failure.

**Caveat:** all but 2 games predate the E58 fix. Re-run after about 300
post-fix first-seat games (live-play's E58 window) to see whether the gap
widens.

Script: `data/orchestrator/scratch/live-loss/seat_split.py [N]` (cache in
`data/orchestrator/analysis/seat_split.json`; per-game facts only, no names).
