# How rehex-2.11 plays (openhex, Oct 19, 10:10 PM Toronto)

The owner asked for this at 10:01 PM on Oct 19, through the lead.

## Summary

rehex is a fixed-rule bot. It answers in under a millisecond and keeps an exact count of both hands. Most of its choices follow one simple rule each, and the same rule fires every time:
- **Robber.** It always puts the robber on the hex that costs the opponent the most dice income, minus whatever it would cost itself (509 of 513 placements). It never blocks its own hex (0 of 509).
- **Knights.** If the robber sits on its own hex and it holds a playable knight, it plays that knight before rolling, every time (103 of 103). Otherwise it mostly holds its knights (27 plays in 252 such turns), and 13 of those 27 won it Largest Army.
- **Cities first.** It never buys a dev card when it could build a city (0 of 377 buys), and it never ends a turn able to build a city (0 of 1,949 turns). It always upgrades its best-producing settlement (125 of 125 cities).
- **Hand limit.** It ends its turn holding 10 or more cards only 6 times in 1,949 turns. It trades with the bank to get under the limit, even when the trade builds nothing (134 of 508 trades). It always gives away its biggest pile (92%) and never trades before rolling.
- **Monopoly.** It always names the resource the opponent holds the most of (27 of 27), and always after the roll.
- **Discards.** It always discards from its biggest pile first (20 of 20).

Its weak spots are a plain opening (no ports, ore-heavy), few roads (0.1 to 0.25 a turn), a 4:1 trade habit, and being fully predictable. Against us it is 5-11. Against the other bots, from the public replays already on disk: 8-4 against catanrl, 0-7 against Asterism, 5-4 against the ar-* bots.

## Public sources (step 1)

- Its site description reads: "Rehex 2.11: a rules-based Catan bot. Code: dev-hex 4b386577." The 8-character code looks like a commit id from a private repository.
- hexn-4d.0 has the same author: "HexN 4d.0: a neural-network Catan bot (self-play reinforcement learning, MCTS 128). Code: dev-hex 36a277ed." It is the same codebase at another commit. rehex has a 4-player entry too, registered within the same second as the 1v1 one (Oct 17, 11:47 AM Toronto), so the registration was scripted.
- Web searches for "dev-hex", "devhex", "rehex catan", "hexn catan", and OpenHex with bot names found nothing: no GitHub, PyPI or other public page. The only public code on the ladder is catanrl's (github.com/mcrco/catanrl), which is a different author.
- Nothing is quoted beyond the two bot descriptions on the site.

## Data (step 2)

- **46 completed games:** 16 of ours against rehex (data/openhex/games) and 30 public replays that include rehex, already on disk from Oct 17 (nothing new was downloaded).
- The public replays show which dev card each buy drew. In our games we only see a card when it is played.
- Both hands were tracked exactly from the events (1v1 steals are visible to both sides). The count never went negative in 1,949 rehex turns.
- Script: data/orchestrator/scratch/openhex/rehex_rules.py

## Rules table

"Fixed" means the same situation gave the same move every time it was seen. "Leaning" means a strong tendency with exceptions.

| Decision | What rehex does | Evidence (count) | Kind |
|---|---|---|---|
| Robber target (after a 7 or a knight) | The hex with the most opponent income minus its own income (pips, with cities counting double) | Best by that measure in 196 of 196 knights and 309 of 313 sevens. The other 4 are probably our approximation of the friendly-robber rule. | Fixed |
| Robber on its own hexes | Never | 0 of 509 | Fixed |
| Robber tie-break | When two or more hexes tie, it leans toward the more likely number. Hex order doesn't explain it. | 135 of 173 ties go to the highest-pip number. Highest or lowest hex index: 67 and 60. | Leaning (not fully explained) |
| Knight timing | Plays the knight before rolling | 178 of 196 knights | Leaning (follows from the next row) |
| Knight when its hex is blocked | Always plays a playable knight to free its hex | 103 of 103 turns (public replays, card known) | Fixed |
| Knight otherwise | Holds it. It plays when that takes Largest Army (13 of 13) and seldom otherwise (13 of 177). Already holding Largest Army: 1 of 62. | 27 plays in 252 turns | Leaning |
| Knight count | 4.3 knights per game. It buys about 8.2 dev cards a game; knights are 55% of draws (141 of 255), as the deck would give. | 196 knights, 377 buys | - |
| City or dev card | City always comes first: never buys a dev card when it could build a city, and never ends a turn able to build a city | 0 of 377 buys; 0 of 1,949 turns | Fixed |
| Which settlement becomes a city | The one with the most pips | 125 of 125 | Fixed |
| When the first city comes | Often right after setup. In 21 of 42 games it upgraded with only its 2 starting settlements; 9 waited until it had all 5. Median first city: its 12th turn. | 42 games with a city | Leaning |
| Dev card buys | Whenever it can't build a city. It buys even at 12+ points (22 buys), so it doesn't race in a special way at the end. | 377 buys: 48 / 101 / 117 / 89 / 22 at 0-2 / 3-5 / 6-8 / 9-11 / 12+ points | Leaning |
| Bank trades: when | Never before the roll. Its hand is usually 8-12 cards when it trades (median 10). | 508 of 508 after the roll | Fixed |
| Bank trades: what | Gives its biggest pile. Asks mostly for ore (190) and wheat (158), then sheep 66, brick 54, wood 40. Rates: 4:1 343, 3:1 117, 2:1 48. | 467 of 508 give the biggest pile | Leaning |
| Bank trades: why | Next action in the turn: dev card 132, settlement 93, city 45, road 23, nothing 134. The "nothing" trades fit a "get under 10 cards" rule. | 427 turns with a trade | Leaning |
| Hand limit (discard at 10+) | Ends its turn with 10 or more cards almost never. It does end with 8-9 often, because 7 is not the limit here. | 6 of 1,949 turns at 10+; 213 at 8+ | Fixed (practically) |
| Discards | Discards half, starting from its biggest pile. It drops wood and sheep most (61% and 60% of those held), ore least (31%). | 20 of 20 from the biggest pile | Fixed |
| Monopoly | The resource the opponent holds most of (it counts cards). Always after the roll. Takes 3.8 cards on average. | 27 of 27 | Fixed |
| Year of Plenty | Picks the two cards that complete a build. After it, a settlement is affordable in 20 of 33 cases and a city in 6. | 33 plays | Leaning |
| Opening, first settlement | High pips with an ore lean, 3 resource types (38 of 46), never on a port (0 of 46). Only half the picks are the top pip total (23 of 46; 14 more are second). A simple score (pips with ore weighted 1.5 and sheep 0.5, plus a bonus per resource type) picks exactly its spot in 37 of 46. | 46 openings | Leaning toward a formula |
| Opening, second settlement | Fills in missing resources: 2 new types in 34 of 46 and 1 in 10. It ends with both ore and wheat in 45 of 46 games and both wood and brick in 39. It gives up pips for this (top pip spot only 14 of 46). Fewer second picks fit a formula (24 of 46). | 46 openings | Leaning |
| Distance to the opponent | No sign it blocks or crowds the opponent. Its second settlement is 2 steps from an opponent settlement in 16 of 46, spread out otherwise. | 46 | No rule seen |
| Later settlements | Median 8 pips. 22 of its 47 port settlements are 3:1 ports. | 171 settlements | - |
| Roads and Longest Road | Few roads: 0.11 a turn with no Longest Road holder, 0.17 when the opponent holds it, 0.25 when it holds it. It builds a little more when threatened, but not much. It took Longest Road 30 times; its opponents took it 39 times. | 1,949 turns, 316 roads | Leaning (weak response) |
| Endgame (12+ points) | The same as before: roll, trade, dev card or build. No sign of racing or blocking. | 51 turns | No special rule seen |
| Player trades | None (OpenHex has no player-to-player trades) | - | - |

## Against us, and against the others (step 3)

Our record against rehex is **11-5** over 16 completed games (two more were aborted). Opponents.md, from 600 public games (Oct 17), also had rehex 3-7 against ar-PXns-a and 0-7 against Asterism.

**How we beat it (11 wins):** We hold Longest Road in 10 of 11 wins and Largest Army in 7 of 11. Across the 16 games we held Longest Road at the end 13 times to its 3. Its weak road play is the opening we use. Those wins are also shorter (84 turns, against 94 in our losses).

**How it beats us (5 losses):** It won with 4.0 cities on average (the most allowed is 4) and Largest Army in 4 of 5. Those games run long (94 turns). That is its city-first, knight-to-unblock plan working when we don't close the game early. Two of its rules cost us in every game:
- Its robber always lands on our best hex. Over 16 games it robbed us 8.9 times a game; we robbed it 10.3 times.
- It counts cards perfectly. That shows mainly in its Monopoly, though it played only 0.2 a game against us (we played 1.1).

**What it would need to beat the ar-* bots** (5-4 in the replays here, 3-7 against ar-PXns-a in the bigger list): The ar-* bots build roads hard: they held Longest Road in 28 of their 31 wins against us. rehex barely answers a road threat. It also trades 4:1 down to 9 cards even when nothing gets built, and never opens on a port. Its robber and knight play are already as good as a fixed rule can be.

**One exploitable fact for us:** its robber is fully predictable. We can tell in advance which of our hexes it will block next: the one with our most income that has none of its own buildings. Our search doesn't use that; it models the opponent as itself.

## Candidates for research (sent to research)

1. **Self-play opponent:** a "rehex-like" fixed-rule player is cheap to write from this table (about 8 rules, all fixed or nearly). It could join a league as a cheap exploiter test.
2. **Input feature:** "the hex the opponent's robber would hit next" (most of our income minus theirs) as one flag per hex. rehex's rule matches its own placements in 509 of 513 cases, and people probably aim the same way.
3. **Input feature:** each player's hand size against the discard limit. rehex always trades down below it, so "cards over the limit" signals a forced trade or discard next turn.
