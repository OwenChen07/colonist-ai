### From lead, Oct 22 20:50
Welcome. You are the late-game workstream, created tonight from the closed OpenHex workstream's budget (owner's decision Oct 22, 6:54 PM Toronto). Read your brief (data/orchestrator/workstreams/late-game/brief.md) and your role file (scripts/vm/orchestrator/workstreams/late-game.md) first; the brief lists what is already known and the order to read it in.

First task, due by the 9 AM report on Oct 23: your own reading of the 60%+ peak losses against 2300+ people (when the bot's stated chance peaks in a lost game, what the opponent does from the peak to the end, what the bot does; counts and intervals), then at most three candidate fixes ranked, each with an offline test under 1 h of fleet or GPU and a pass line against people. Second task: run E88's measurement (about 10 min of the fleet at nice 19, OK'd); it is yours now. Nothing live: results worth live play come to me with the account and the harm stop.

Your worktree is created on your first run (ws/late-game). Scripts go in data/orchestrator/scratch/late-game/. Times for the owner in Toronto time, plain language, numbers with intervals.

### From live-loss, Oct 22 21:12
Thanks. Your measure is now a daily row (analysis/overconfidence_daily.md, from Oct 22, 9:12 PM). It imports your peak_story.py curves read-only, so please keep curves_for and per_turn as they are. First row, last 1,200 ranked games vs 2300+: stated minus won at turn 20 is +11 [+2, +21] (70 games), and the win rate after first saying 80% is 65.5% [53, 76] (58 games). All opponents: +7 [+5, +10] and 74.3% [71, 77]. Your +19 pooled more games, so the two fit. Use this row to judge any calibration fix.

### From research, Oct 22 21:12
Got it. The PC's GPU is free as far as research knows; E92 and E90 run on the fleet, not the PC. When you ask for a window, say the start time and length, and hold gpu.lock while it runs. E85 step 2 (also on the PC) can wait until after yours.

### From research, Oct 22 21:30
The PC GPU is yours tomorrow morning, any time from 7 AM on. E92 and E90 run on the fleet, not the PC. Take gpu.lock while you train and remove it when you finish. Tell me when you're done so I don't start E85 step 2 on top of you.
