# Is the exact-hand export (opponent_exact) biased? (research's E55 questions)

Oct 19, about 10:30 AM Toronto. Train split, 3,817 games, all replayed (no errors, no mismatches).
The person's decisions with more than one legal move, setup left out (that's why the
total is below the index's 182,587 rows, which include setup moves).
Intervals are Wilson 95% per decision. Decisions in one game are correlated, so the real
intervals are wider; the gaps below are far bigger than that.

Colonist reveals only victory point cards at the end. A knight (or any action card) held to the
end and never played can't be identified, so every decision while it's held is dropped.

## (1) Share of the person's decisions kept

| | all games (3,817) | games the person won (1,640) |
|---|---|---|
| decisions kept | 167,351 of 332,682 = 50.3% | 78,009 of 179,458 = 43.5% |
| whole turns kept | 60.5% of 129,890 | 53.1% of 63,442 |
| turn 0-30 | 79.4% | 76.2% |
| turn 30-60 | 48.5% | 42.3% |
| turn 60-90 | 31.1% | 26.5% |
| turn 90+ | 51.7% | 48.5% |

## (2) Cards held at turn 60+: kept against all

| turn 60+ | all decisions | kept | dropped |
|---|---|---|---|
| decisions | 121,407 | 40,659 (33%) | 80,748 |
| dev cards held, mean | 2.22 | 1.49 | 2.59 |
| 1+ held | 89.4% | 68.3% | |
| 2+ held | 65.0% | 42.2% | |
| 3+ held | 38.4% | 24.0% | |
| action cards held, mean | unknown (that's the point) | 0.48 | |

Games the person won, turn 60+: held 2.44 in all against 1.70 kept; 3+ held 44.4% against 29.3%;
action cards in kept hands 0.56.

## Hoarding games

1,451 of 3,817 games (38%) have an action card held to the end, unplayed. In them, 39.3% of
decisions are kept overall but only 13.2% at turn 60+ (other games: 58.0% and 47.8%).
Person-won games: 717 of 1,640 (44%); 9.9% kept at 60+.

## Reading

Research's worry holds. The export keeps half the person's moves, but late in the game it keeps
a third, and it keeps them from small hands: kept late positions hold a third fewer cards, and
the games where people sit on a card to the end almost disappear from the late game (13%).
A people model trained only on opponent_exact will see too little late hoarding.
Possible mitigations (research's call): weight kept late decisions from hoarding games up; or
use the known-count, unknown-type decisions too with the hidden cards encoded as "some
action card" (the encoding would need an unknown-card input).

Script: data/orchestrator/scratch/live-loss/exact_bias.py [LIMIT] (about 5 min, 6 processes).
