# Our games against [opponent A] (internal, Oct 22, about 1 PM Toronto)

Owner's request via the lead (Oct 22, 12:49 PM). The name stays in this file, progress.md and the report to the lead.

Search: every capture on this machine (captures/ranked, bot2-bot12, 6,694 files), the player's name matched case-insensitively ("phantom"). Exactly one name matched: [opponent A]. **8 games**, Oct 15-21. None in the last 36 hours.

## 1. The games

Results come from games.jsonl. Ratings are colonist's MMR before the game. Turns count both players.

| When (Toronto) | Account, net | Placed first | Bot / their rating | Result | Turns | Final VP bot-them | Colonist game id |
|---|---|---|---|---|---|---|---|
| Oct 15, 1:57 AM | bot5, round 40 | bot | 2177 / 2240 | **win** | 86 | 15-9 | 264215065 |
| Oct 15, 9:54 PM | bot4, round 40 | bot | 2175 / 2259 | **win** | 62 | 15-6 | 264418284 |
| Oct 15, 10:05 PM | bot4, round 40 | them | 2188 / 2241 | loss | 72 | 10-15 | 264420027 |
| Oct 18, 5:13 AM | bot5, round 73 | them | 2319 / 2313 | **win, they resigned** | 33 | 7-3 | 264917642 |
| Oct 18, 10:04 PM | Dilly6283 (bot3), round 73 | them | 2288 / 2229 | loss | 76 | 10-15 | 265085760 |
| Oct 18, 10:23 PM | Dilly6283 (bot3), round 73 | bot | 2269 / 2242 | loss | 93 | 10-15 | 265088118 |
| Oct 20, 11:25 PM | Dilly6283 (bot3), round 73 | bot | 2381 / 2274 | loss | 95 | 11-15 | 265588514 |
| Oct 21, 12:00 AM | Tedi4960 (bot9), E54 step 30 | bot | 2064 / 2298 | loss | 79 | 10-15 | 265594993 |

Colonist ids are the capture's databaseGameId (room codes, same order: sail8659, hand6939, mine9300, land7908, clay334, mine3338, sheep831, play1421).
All losses ended by reaching 15 points; no time-outs. Two pairs were back-to-back rematches (Oct 15 9:54/10:05 PM, Oct 18 10:04/10:23 PM).

**Record: 3-5 (38%)**, vs 48% (108-119) against every 2300+ opponent. Difference -10 points ±36: no evidence they are harder than the band.
**Their rating when they met us: 2229-2313, average 2262**, below the 2300+ band's average of 2388. Whatever they are now, in our data they played as a mid-2200s player.

## 2. What they do differently (8 games vs 227 games against 2300+ opponents)

Same replay for both groups. "±" is a 95% interval on the difference.

| | Them | 2300+ band | Diff |
|---|---|---|---|
| Opening pips (2 settlements) | 20.5 | 21.3 | -0.8 ±0.6 |
| Opening ore+wheat pips | 9.0 | 9.2 | -0.2 ±2.4 |
| Opening wood+brick pips | 7.0 | 8.2 | -1.2 ±2.1 |
| Resource kinds touched | 4.6 | 4.6 | 0 |
| Turns 1-10: roads / settlements / cities / dev cards | 1.1 / 0.6 / 0.1 / 1.1 | 1.4 / 0.6 / 0.3 / 0.7 | dev cards +0.5 ±0.6 |
| First city (own turn) | 18.8 | 16.7 | +2.1 ±4.6 |
| Dev cards bought | 7.4 | 8.0 | -0.6 ±3.4 |
| Knights / monopoly / year of plenty / road building | 3.3 / 0.3 / 0.8 / 0.6 | 4.1 / 0.6 / 0.5 / 0.6 | all inside the noise |
| VP cards at the end | 1.9 | 1.7 | +0.2 ±1.2 |
| Robber on the bot's best hex (clean moves) | 29% | 44% | -15 ±20 |
| Robber blocks their own hex | 2% | 6% | -4 ±3 |
| Bank/port trades per turn | 0.18 | 0.18 | 0 |
| Ever held longest road / largest army | 38% / 25% | 56% / 45% | -19 ±36 / -20 ±33 |
| Largest army, first held (own turn) | 18.5 (2 games) | 28.8 | early, n=2 |

Opening ports: my replay found almost none on either side (0-2%), so I don't trust that count and leave it out.
Player trades: the bot never trades, so there is nothing to measure there.

Their wins look like this: a slow ore-wheat or mixed opening, few early roads, then many dev cards (14, 7, 13, 11 and 2 bought in their five wins; 5, 6 and 1 in their three losses), cities, and 2-4 VP cards hidden at the end. That is the normal winning shape at 2300+; only the robber number and the early dev buys lean away from the band, and neither is outside the noise.

## 3. How we lose to them

Win-chance curves from the bridge logs (bot's own estimate at each of its moves):
- **All 5 losses had the bot at 60% or more first.** Peaks: 66, 78, 79, 75, 86; median 78. Against the 2300+ band, 86% of losses [78, 91] (102/119) went over 60%, median peak 79. **Same pattern, not specific to them.** This is the known over-confidence on the bot's chair (E84/E88).
- The biggest single drops all fall on a dice roll or the opponent's turn, not on a bot choice: e.g. 78% → 46% on a 7 that made the bot discard (Oct 18, 10:23 PM); 86% peak at move 11 then a slow slide (Oct 21); 79% → 53% after a roll (Oct 20). The search's step-to-step value never shows one bad move; the losses are slow slides while they buy cards.
- Predicting their moves (positions with an exact hand, 399 moves in 8 games):

| | People model top-1 / cross-entropy | Main net top-1 / cross-entropy |
|---|---|---|
| Them, all 8 games | 74.9% / 0.676 | 67.4% / 0.944 |
| Band, all 225 games (9,955 moves) | 73.2% / 0.744 | 66.8% / 0.974 |
| Them, held-out games only (2 games, 126 moves) | 70.6% / 0.767 | 61.9% / 1.064 |
| Band, held-out only (28 games, 1,029 moves) | 70.8% / 0.844 | 64.3% / 1.126 |

6 of their 8 games are in the people model's training set, so the "all" row flatters it. On held-out games they are as predictable as the band. **Neither model finds them harder to read.**

## 4. What they get wrong (our 3 wins)

- Oct 15, 9:54 PM (15-6): they took a wood-brick start (5 ore+wheat pips), bought 5 cards and never got a city until turn 18. The bot led from move 1 (64%) and never dipped.
- Oct 15, 1:57 AM (15-9): bot fell to 29% early, then recovered to 100% as they built only one city by turn 25.
- Oct 18, 5:13 AM: they resigned at turn 33, 3 points to our 7, after a 19-pip opening and one dev card.
- In all three they bought few dev cards (5, 6, 1) and placed the robber on the bot's best hex 0-25% of the time. When their dev-card engine doesn't start, they lose. One quick resignation is not a pattern.

## 5. For the owner

Eight games is enough to describe, not to conclude. Every difference between this player and the usual 2300+ opponent is inside the noise; the only leanings are fewer pips in the opening, earlier dev cards, a looser robber and an early largest army in the two games they took it. At the time they met us they were rated about 2260, below our usual top opponents, and our record against them (3-5) is within chance of our 48% against the whole 2300+ band. The way they beat us is the way everyone at that level beats us: the bot gets to 60-85% sure, then the opponent's dev cards and cities catch up while the bot stays too confident. That is the chair bias the research workstream is already working on (E84, E88), and it does generalise to the 2400+ band, because it is present in 86% of all our losses to 2300+ players. I would not build anything specific to this player.

## Details
Scripts: data/orchestrator/scratch/live-loss/phantom_find.py, phantom.py (scan), phantom_report.py, phantom_curve.py, phantom_peak.py, phantom_model.py. Stats: data/orchestrator/analysis/phantom_stats.json. Captures in table order: bot5/bridge-20261015-055717, bot4/bridge-20261016-015447, bot4/bridge-20261016-020519, bot5/bridge-20261018-091307, bot3/bridge-20261019-020451, bot3/bridge-20261019-022343, bot3/bridge-20261021-032551, bot9/bridge-20261021-040025. Models: runs/g1/best.npz (main), models/human/net.npz (people).
