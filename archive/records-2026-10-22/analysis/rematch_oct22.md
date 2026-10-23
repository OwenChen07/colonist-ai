# Rematch study (owner's question, Oct 22, 11:58 AM Toronto)

Done Oct 22, about 12:45 PM Toronto. Script: data/orchestrator/scratch/live-loss/rematch.py (scan cache: data/orchestrator/analysis/rematch_scan.json, opponent names hashed).
Data: every account's ranked games against people, Oct 7, 12:32 AM to Oct 22, 12:10 PM: 6,365 games, 6,218 back-to-back pairs with both opponents known.
Gap = previous game's last capture frame to the next game's first game-state frame (includes the queue). Median 14 s, 90% under 50 s.

1. Next game against the same opponent:
   - after a win: 11.0% [10, 12] (405 of 3,672)
   - after a loss: 7.6% [7, 9] (194 of 2,546)
   - by gap: under 1 min 10.4% (595 of 5,746); 1-3 min 1.1% (4 of 362); over 3 min 0 of 110.
2. Our win rate in the rematch:
   - rematch right after a loss: 59.3% [52, 66] (115-79)
   - rematch right after a win: 56.3% [51, 61] (228-177)
   - all games: 59.0% [58, 60] (3,755-2,610)
   - same 100-point rating band: rematch-after-loss vs all games +1.1 pts [-5.6, +7.9]; vs rematch-after-win +3.2 [-5.1, +11.3].
   Verdict: a player who just beat us does not beat us more in the immediate rematch. The range rules out a cost bigger than about 6 points.
3. No fix proposed. Even at the worst end of the range (-5.6 pts), about 13 such rematches a day across all accounts would cost under one extra loss a day in total.
   If ever wanted: rematches happen almost only when both requeue within a minute, so a 1-minute requeue delay after a loss would remove nearly all of them.
4. Top band: Tedi4960 (bot9) since Oct 21, 7:45 PM: 10.1% [5, 19] (8 of 79 pairs); after a loss 4 of 23 (17%, range 7-37); 80 games, 67 different opponents, most-met opponent 3 times.
   All accounts, next opponent rated 2400+: 17.8% [11, 28] (13 of 73). Bot rated 2300+: 13.1% [11, 16] (86 of 657). So the rematch rate roughly doubles at the top, as the pool thins.
