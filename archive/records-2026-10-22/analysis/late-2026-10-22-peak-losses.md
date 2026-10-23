# Late-game: why we lose games we rated 60-85% won against strong people (first reading)

Late-game workstream, Oct 22, 2026, 9:10 PM Toronto; headline added 9:55 PM. For the 9 AM report (Oct 23).

## Headline
- **No late collapse.** In 2300+ losses the bot's chance peaks early (median bot turn 12 of about 41), on an even board and a thin lead (+0.5 VP). The strong person then out-builds us: 2.8 cities and 4.1 settlements to our 1.35 and 2.0, Longest Road in 46 of 52, Largest Army in 39 of 52. Their dice edge from the peak is 26 cards; 21 of them come from the board they build, 4 from luck.
- **The net is over-sure, and more so turn by turn and against stronger people:** stated minus won at bot turn 20 is +8 under 2100, +12 at 2100-2300, +19 at 2300+. No single position type carries it (section 3c). Self-play labels can't fix it (E84), and the people model doesn't play like strong people (E88, dead: the bot does better against it than against itself).
- **Fix 1 (E93) is queued:** a value fine-tune on our real games against 2100+ people, on the PC's GPU from 7 AM. Baseline on the 318-game 2100+ holdout: E85 step 1 is +8.2 [+4.7, +11.9] too sure. Pass: down by at least 4, paired. Then 2,000 games at 49% or better. Nothing goes live without the lead.
- Fix 2 (slice weighting) failed. Fix 3 (Longest Road defence in the search) is second.

## Data
- 5,110 ranked games of all accounts with the live search's stated win chance on every move (bridge.log), 5,082 with the opponent's rating: 2,998 under 2100, 1,848 at 2100-2300, **236 at 2300+** (77 at 2400+).
- The 236 games against 2300+ replayed move by move (all 236 replayed cleanly): who built what, bought and played which cards, robbed whom, and the dice income of each side, counted from the bot's peak and from the first time it said 70%.
- "Bot turn" = the bot's own turns (one game turn is about two of them). A game against 2300+ lasts a median 41 bot turns when we lose, 34 when we win.

## 1. The 60%+ peak is mostly the start, not the end
- Against 2300+ the bot says **57% right after setup** and wins **48%** (+9 points too sure, 95% +3 to +15). It already says 60% or more by its first roll in most games: 91% of 2300+ games cross 60%, at a median bot turn 1.
- So "86% of losses had a 60%+ peak" (live-loss; my count 82% [74, 88], 100 of 122) is mostly built in: even a perfectly honest 48% bot would show a 60%+ peak in roughly 60-65% of its losses, because the highest point of a wandering chance is above where it starts. The over-sureness adds the remaining 20 or so points.
- The peak in a lost game comes early: median **bot turn 12** (IQR 6-20), with 30 bot turns still to play (IQR 24-37). Only 11 of the 100 losses with a 60%+ peak had it in the first 3 bot turns; 65 had it after bot turn 10.

## 2. The over-sureness grows with the opponent's rating and with every turn
Stated minus won, points, all games whose stated chance was 10-90% at that turn (95% bootstrap by game):

| opponent | after setup | bot turn 10 | turn 15 | turn 20 | turn 25 |
|---|---|---|---|---|---|
| under 2100 | −3.8 [−5.4, −2.1] | +3.9 [2.1, 5.6] | +5.5 | +7.8 | +10.1 [7.7, 12.3] |
| 2100-2300 | +3.1 [1.0, 5.4] | +9.8 [7.4, 11.9] | +11.0 | +12.0 | +15.0 [12.3, 17.7] |
| 2300+ | +9.0 [3.0, 14.9] | +14.5 [8.5, 20.2] | +14.2 | +19.2 [11.7, 26.8] | +22.2 [15.1, 29.5] |

- Once it first said 80% against 2300+, it won **65% [58, 72]** (175 games); under 2100 it won 76% [74, 78]. Once it said 70%: 58% [51, 65] against 2300+, 69% under 2100.
- Reading: the net's chance is about right against weak people at the start and drifts in the bot's favour turn by turn, faster the stronger the person. That is what a value learned in self-play does against an opponent who plays the middle game better than the net's own copy: it keeps expecting the opponent to play like itself. This agrees with E84 (the bot's chair over-rated on people's positions, +3.4 at the start rows) and E71 (by people's turn 50-90 the result is settled).

## 3. What happens from the peak to the end (2300+ losses)
Losses whose peak was 60-85% (52 games; peak median 73% at bot turn 9; VP lead at the peak only +0.5 [0.1, 1.0]):

| from the peak to the end | bot | person |
|---|---|---|
| resource cards from dice | 63 | **89** (+40%) |
| cities built | 1.35 | **2.83** |
| settlements built | 2.0 | **4.1** |
| roads built | 6.0 | 8.6 |
| bank trades | 6.0 | 9.0 |
| development cards bought | 6.8 | 8.5 |
| knights played | 3.4 | 4.4 |
| robber moves that rob the other | 7.6 | 8.8 |
| holds Longest Road at the end | 6 / 52 | **46 / 52** |
| holds Largest Army at the end | 12 / 52 | **39 / 52** |
| VP cards at the end | 1.8 | 2.0 |

The person ends at 15 points from about 3.5 settlements, 3.2 cities, 2 VP cards, Longest Road (88%) and Largest Army (75%). The bot ends at 9.

**Control, same moment in wins:** from the first time the bot said 70% (by bot turn 15; VP level at that point in both), 72 losses vs 106 wins against 2300+:
- dice income per 10 bot turns: losses bot 22.3 / person 26.7; wins bot 24.8 / person 19.4. The side with more income wins either way, by about 20% in both directions.
- Longest Road: the person takes it in **60 of 72 losses (83%)** but only 23 of 106 wins (22%). Largest Army: 48 of 72 (67%) vs 12 of 106 (11%).
- Cities per 10 turns: losses bot 0.50 / person 0.81; wins bot 0.85 / person 0.51.

**Reading:** there is no late collapse and no single bad move (live-loss found the same in the search's trace). From an early and thin lead (+0.5 VP), the strong person out-produces us, out-builds us on cities and settlements and wins both prizes, Longest Road in 5 of 6 losses. The loss is decided in the middle game (bot turns 5-25), while the net still says 70-85%.

## 3b. The income gap is the board the person builds, not the dice (added Oct 22, 9:20 PM)
For every roll, each side's expected yield (the pips of its settlements and cities, the robber where it stood) against what the roll actually paid. Luck = paid minus expected; robber cost = expected without the robber minus with it. Same 2300+ games, 95% bootstrap by game.

| 52 losses with a 60-85 peak, peak to end | bot | person |
|---|---|---|
| cards paid by the dice | 63 | 89 |
| cards the board was expected to pay | 66 | **87** |
| luck | −2.6 [−4.1, −1.1] | +1.8 [0.0, 3.5] |
| cards lost to the robber | **8.8** [7.7, 9.9] | 5.2 [4.0, 6.5] |

- Of the person's 26-card edge, **21 [17, 26] is the board** and 4.4 [2.2, 6.7] is dice luck (about one sixth).
- **At the peak the boards are even:** 0.90 vs 0.87 cards a roll (robber aside). By the end the person's board pays 2.31 a roll, the bot's 1.73. From the first time the bot said 70% (by bot turn 15): even at the crossing in both losses (0.68 vs 0.66) and wins (0.66 vs 0.65); at the end, losses 1.87 vs **2.28**, wins **1.93** vs 1.54. The bot's own board ends about the same in wins and losses (1.9 vs 1.9); what differs is how far the person grows theirs.
- The robber costs the bot 9.5 cards in a lost game against 2300+ and 5.1 in a won one; the person loses about 6 either way. In losses strong people put the robber on the bot's best tiles and keep it there longer (they also hold Largest Army in 75%).

**Reading:** the bot's 70% is said on an even board, early, and the result then turns on how fast the person turns that board into cities and settlements. The net expects the person to grow like its self-play opponent; a strong person grows faster. That is a judgment of the opponent, not bad luck and not one bad move, which points to labels from real games against strong people (fix 1) and to the robber (a new fix 3 candidate below), not to a search tweak.

## 3c. The over-sureness is everywhere, not in one kind of position (added Oct 22, 9:25 PM)
Stated minus won in the 237 replayed 2300+ games, at bot turns 10, 15 and 20, sliced by what the person holds at that turn (slice_report.py; 95% ranges by game). Whole set: **+13.9 [8, 20]**, +12.8 [8, 18], +13.6 [9, 19].

| slice (bot turn 20 unless noted) | n | said | won | gap |
|---|---|---|---|---|
| person holds 0 hidden dev cards | 92 | 65 | 54 | +11 [3, 19] |
| 1 | 72 | 59 | 39 | +20 [11, 29] |
| 2+ | 59 | 48 | 37 | +11 [1, 20] |
| nobody holds Longest Road, lengths within 2 | 161 | 58 | 42 | +15 [10, 21] |
| person holds Longest Road | 26 | 58 | 54 | +4 [−12, 19] |
| person 0 / 1 / 2+ cities | 93 / 80 / 50 | | | +15 / +13 / +12 |
| person ahead on cities / even / bot ahead | 56 / 82 / 85 | 43 / 56 / 71 | 36 / 40 / 55 | +8 / +16 / +16 |
| robber steals on the bot so far 0 / 1 / 2+ | 16 / 27 / 180 | | | +5 / +25 / +13 |
| person's knights ≥ bot's, Army unclaimed | 66 | 47 | 30 | +17 [8, 26] |
| turn 10, person ahead on cities | 31 | 53 | 29 | +24 [8, 41] |

- **No slice carries half the gap** with a range clear of zero; every large slice sits at +11 to +17, the whole set's level. Fix 2's pass line fails: there is no single position type to weight.
- The one place the gap closes is where the danger is already visible (the person holds Longest Road, or is ahead on cities): the net reads a visible lead correctly. It is over-sure where the position still looks even, i.e. it expects a strong person to convert an even board the way its self-play opponent does (section 3b).
- Hidden dev cards and the robber don't separate: 1 hidden card is +20 but 2+ is +11; 0 steals is +5 on 16 games only.

## 4. Candidate fixes, ranked (offline test under 1 h each, pass line against people)
1. **Value fine-tune on the bot's own games against strong people, weighted up.** Production already learns its value from our live games, but at about 3% of positions, mostly against people under 2100; E84 showed self-play labels cannot correct this (they are calibrated against self-play). Real outcomes against 2100+ people are the only labels that carry the drift in section 2. Test: fine-tune the top net's value (as E85 step 1: policy held, KL to its own policy) with the training split's 2100+ live games repeated so they are about 15-20% of the batch; about 30 min of the PC's GPU (research's OK, gpu.lock). **Pass:** on the 2100+ holdout (paired by game), the stated-minus-won gap from bot turn 10 on falls by half (2100-2300 from about +11 to under +6), the 2100+ value error does not rise (step 50 0.647, E85 0.623), and 2,000 games at 200 simulations against the base net score at least 49% (it must not get weaker). **Risk:** it may only lower the stated number without changing moves; the live read is the real test. This is training's recipe area: I build and test it offline only, and tell research and training.
2. ~~**Find where the drift sits before training on it.**~~ **Done 9:25 PM, failed its pass line (section 3c):** the gap is spread evenly, so fix 1 weights all 2100+ games, not one position type. Fix 3 moves up to second. The original text: Split the 2300+ and 2100+ drift by (a) the person's hidden development cards (people hold 1.56 on average, self-play's person 0.40: the net has rarely seen a hoarding opponent), (b) a Longest Road race within 2 roads, and (c) the person's city count. If one of them carries most of it, fix 1 weights that position type instead of all games, which is more likely to change moves (for example defending Longest Road earlier). **Pass:** one slice carries at least half the gap with a range clear of zero.
3. **Longest Road defence in the search.** People take Longest Road in 83% of our 2300+ losses after we said 70% (22% in wins), and E82 found the net misjudges a thin road lead (said 52%, won 27%). A search-side correction (a road-race term at the leaves, or more simulations when a road race is within 2) can be tested offline from E82's road-race positions of people's games against the live result. **Pass:** from those positions, the bot's chair keeps Longest Road at least 5 points more often (1,000 starts, paired), with no loss in the 2,000-game self-play match. Lower rank: more code, and the cause may be the income gap, not the road choice.

**Not recommended:** the people model as the opponent's policy inside the search (E71 and E79: the bot wins as much or more against it as against itself, so it would make the search expect weaker replies and be more over-sure); a think-time split (the slide is many small turns, not one hard decision, and live searches are already time-capped on 61-86% of moves); a KL to the people model late (no sign the bot's own moves are the problem rather than its judgment of the person's).

## Still to do before 9 AM
- ~~Split the income gap into dice and board~~ done, section 3b: mostly board.
- ~~Fix 2's slices and the robber slice~~ done, section 3c: nothing carries the gap; the robber does not rank above the road fix.
- ~~E88's rerun on step 50~~ done 9:27 PM, dead: the bot's chair wins +2.4 [+0.9, +4.0] points more against the people model than against itself (2,085 paired starts; 2100+ +1.8 [−1.3, +5.0]). From the 2100+ starts real people beat it most (live 45.7%, self 48.1%, people model 49.9%). This leaves fix 1 (real games against strong people) as the only label source that carries the gap.
- Fix 1 is **E93**, queued 9:48 PM: research gave the PC's GPU from Oct 23, 7 AM. Baseline on the people holdout (318 games against 2100+, never trained on): E85 step 1 says **+8.2 [+4.7, +11.9]** more than it wins, value error 0.613 (step 50 +10.6, 0.636); under 2100 +2.3. Pass: the 2100+ gap falls at least 4 points against E85 step 1, paired; error not higher; under 2100 not hurt; then 2,000 games at least 49%.

Scripts: data/orchestrator/scratch/late-game/peak_story.py (curves, ratings, replays to peak_story.json, per-roll expected and paid cards), peak_report.py, luck_report.py (section 3b), open_report.py, gap_ci.py.
