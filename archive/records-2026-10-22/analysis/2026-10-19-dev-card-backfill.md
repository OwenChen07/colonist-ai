# Could we backfill people's hidden development cards? (Oct 19, 9:40 AM)

Asked by the lead (Oct 19, 9:45 AM) for an owner decision. Today the human-game
pipeline keeps a person's move only when they hold no unplayed development
card. This counts how many more of their decisions would have a fully known
hand if we fill the cards in from later evidence (plays, and the end-of-game
reveal of victory-point cards).

**Data:** every train-split game in data/human/index.jsonl: 3,807 games, all
replayed cleanly. 331,750 of the person's decisions (more than one legal move,
the person to move, setup placements left out). This is every game, not a
sample, so the shares carry no sampling error beyond how typical these games
are (with 3,807 games, about ±1 point at the game level).

**Bins at each decision:**
- (a) no card held: what the pipeline uses today.
- (b) card(s) held, the hand is known exactly: every possible way of matching
  later plays and revealed VP cards to the earlier purchases gives the same hand.
- (c) held, every card is identified later, but the hand depends on which
  purchase was which (two or more possible hands).
- (d) held, and a card that is never identified could be in hand (the
  person's unplayed non-VP cards at the end of the game).

A played card must come from a purchase on an earlier turn. VP cards (count
from the end-game summary, present in all 3,807 games) and never-identified
cards can sit on any purchase.

## Result

| Bin | All decisions | Turn < 30 | Turn 30-75 | Turn 75+ |
|---|---|---|---|---|
| (a) none held (today) | 100,784 (30.4%) | 48,215 (61.8%) | 48,947 (24.5%) | 3,622 (6.7%) |
| (b) held, known exactly | 66,188 (20.0%) | 13,728 (17.6%) | 35,836 (17.9%) | 16,624 (30.9%) |
| (c) held, ambiguous | 85,244 (25.7%) | 10,322 (13.2%) | 60,656 (30.3%) | 14,266 (26.5%) |
| (d) held, never identified | 79,534 (24.0%) | 5,735 (7.4%) | 54,509 (27.3%) | 19,290 (35.9%) |
| Total | 331,750 | 78,000 | 199,948 | 53,802 |

Types in doubt in (c) (card kinds whose count differs between the possible
hands, out of knight, monopoly, year of plenty, road building, VP):
2 kinds 34,773; 3 kinds 26,622; 4 kinds 17,697; 5 kinds 6,152.

Never-identified cards: 1,448 of the 3,606 games with any purchase end with
at least one unplayed non-VP card.

**Knight-race games** (the person played 3 or more knights): 2,333 of 3,807
games (61%). Their decisions: (a) 23.6%, (b) 20.1%, (c) 29.3%, (d) 27.1%.
Only 122 of them (5%) have every card-holding decision in (b); 566 (24%)
have half or more in (b). Other games: 668 of 1,474 all in (b), 914 half or
more.

## Reading
- Adding (b) takes the usable decisions from 30% to 50% (+66% more data),
  and most of the gain is mid and late game, where today we have almost
  nothing (turn 75+: 7% today, 38% with (b)).
- (c) is another 26%. It could be used by sampling or weighting the possible
  hands, but the hand then isn't certain.
- (d) stays out unless we guess the hidden card.
- Knight-race games, the ones about Largest Army, gain about as much as the
  rest in decisions (20% in (b)), but few of them are fully covered: the
  person usually holds an unplayed card at some point that is ambiguous or
  never shown.

Details: script data/orchestrator/scratch/live-loss/devbackfill.py [LIMIT]
(a few minutes, 6 processes). "Known exactly" means a unique hand multiset,
which is slightly wider than "a unique matching": two matchings that leave the
same hand both count as (b). Turn is the engine's num_turns (both players'
turns counted). The only timing rule used is that a card can't be played on
the turn it was bought.
