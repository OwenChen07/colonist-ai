"""Search speed per account before and after a cut time (capture names are UTC).
Usage: load_cost.py CUT_UTC(HHMMSS on today's date, e.g. 183500) [FROM_UTC]
Prints, per account and window: games, time-capped PLAY_TURN/ROBBER moves,
median sims of those and median sims per second; ROLL median sims."""

import statistics
import sys
from collections import defaultdict

from clock_table import ROOT, classify, parse_games

DAY = "20261019"
CUT = DAY + "-" + sys.argv[1]
FROM = DAY + "-" + (sys.argv[2] if len(sys.argv) > 2 else "150000")
ACCTS = ["bot3", "bot4", "bot5", "bot7", "bot8"]

for acct in ACCTS:
    try:
        games = parse_games(f"{ROOT}/{acct}/bridge.log")
    except OSError:
        print(acct, "no log")
        continue
    win = defaultdict(lambda: defaultdict(list))
    for name, moves in games.items():
        stamp = name[len("bridge-") : len("bridge-") + 15]
        if stamp < FROM:
            continue
        w = "after" if stamp >= CUT else "before"
        win[w]["games"].append(1)
        for prompt, label, ms, sims, turn in classify(moves):
            if sims <= 0:
                continue
            if prompt in ("PLAY_TURN", "ROBBER") and ms >= 4500:
                win[w]["capped_sims"].append(sims)
                win[w]["rate"].append(sims / ms * 1000)
            if prompt == "ROLL":
                win[w]["roll"].append(sims)
            if prompt.startswith("INIT_SETTLEMENT"):
                win[w]["settle"].append(sims)
    for w in ("before", "after"):
        d = win[w]
        if not d["games"]:
            print(f"{acct} {w}: no games")
            continue
        med = lambda k: int(statistics.median(d[k])) if d[k] else 0
        print(
            f"{acct} {w:6}: games {len(d['games']):3}  capped moves {len(d['capped_sims']):4}  "
            f"med sims {med('capped_sims'):5}  sims/s {med('rate'):4}  roll sims {med('roll'):4}  "
            f"settlement sims {med('settle'):5}"
        )
