# Late-game calibration of the bot's stated win chance (live-play, Oct 19, 4:55 PM Toronto)

Question (task 4, games past 90 turns): is the net "too sure of a late lead"
against people, and is there a search-side lever?

Data: ranked games on round 73 (bot3, bot4, bot5, bot7), last 400 per account,
766 games joined bridge.log to games.jsonl. Stated chance = the search's root
value printed on each searched move (sims > 0). One sample per game and phase:
the mean stated chance in that phase. Phase = the bot's own roll count (total
rolls are about 2x). Each game's last 3 bot rolls dropped, so the final swing
(the opponent wins on its own turn) does not bias losses upward.

Record by final length (bot rolls): 0-14 30-0; 15-29 113-16; 30-44 238-200
(54%); 45+ 65-104 (38.5%). The long-game drop still holds on round 73.

| phase (bot rolls) | stated 20-40% | stated 40-60% | stated 60-80% | stated 80-100% |
|---|---|---|---|---|
| 0-14 | 33 → won 19 (n=91) | 52 → 42 (n=267) | 69 → 74 (n=342) | 85 → 98 (n=60) |
| 15-29 | 30 → 10 (n=95) | 51 → 38 (n=102) | 71 → 75 (n=146) | 92 → 95 (n=243) |
| 30-44 | 28 → 5 (n=58) | 51 → 33 (n=61) | 71 → 62 (n=55) | 94 → 98 (n=197) |
| 45+ | 29 → 13 (n=16) | 50 → 27 (n=11) | 68 → 60 (n=15) | 92 → 91 (n=22) |

Findings:
- The bias is not a late lead. When the bot says 80%+ it wins 91-98%, as often
  or more often than it says.
- The bias is hope when even or behind: from the middle game on, a stated
  20-60% is 13-23 points too high (stated 50 → won 33; stated 28 → won 5).
  This is where long games are decided, so it fits the 38% record past 45 bot rolls.
- People model at the leaves (bot5, mix 0.5) vs none (bot3, bot4, bot7), the
  20-60% bands from 15 rolls on: average gap +16 points (75 samples) vs +19
  (268). A 3-point difference with a standard error of about 5: no evidence
  that the mix fixes it.

Verdict: no search-side lever found. This is the value net against people
(training / research). Candidates for the cause, unchecked: hidden victory
point cards and dev cards (the opponent's hand the bot cannot see), and
people playing close endgames better than the self-play opponent the value
was trained on. A useful next check for live-loss: in losses where the bot
said 20-60% at mid-game, how did the opponent get its last points (VP card
reveal, longest road or largest army swing, cities)?

Script: data/orchestrator/scratch/live-play/late_calib.py N TAIL ACCOUNTS
(`late_calib.py 400 3`).
