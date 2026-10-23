"""What the opening gets now, per account, from bridge.log: the bot's two setup
settlements and roads (ms, sims, sims/s, how often the 20 s or 20k cap binds),
the bot's total think time a game, and the median gap between ranked games.
Usage: open_clock.py ACCTS [N]  (last N games per account, default 60)"""

import json
import re
import statistics
import sys

MOVE = re.compile(r"  move: (\w+).* \(win chance \d+%, (\d+) ms, (\d+) sims\)")
accts = sys.argv[1].split(",")
n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
med = statistics.median


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


for acct in accts:
    games, cur = [], None
    for line in open(
        f"/home/owen/colonist-ai/data/{acct}/bridge.log", errors="replace"
    ):
        if "following: game started" in line:
            cur = {"set": [], "road": [], "think": 0, "moves": 0}
            games.append(cur)
            continue
        m = MOVE.match(line)
        if m and cur is not None:
            what, ms, sims = m.group(1), int(m.group(2)), int(m.group(3))
            cur["think"] += ms
            cur["moves"] += 1
            if what == "settlement" and len(cur["set"]) < 2 and not cur["road"][2:]:
                cur["set"].append((ms, sims))
            elif (
                what == "road"
                and len(cur["road"]) < 2
                and len(cur["set"]) > len(cur["road"])
            ):
                cur["road"].append((ms, sims))
    games = [g for g in games if len(g["set"]) == 2 and g["moves"] > 20][-n:]
    for k in (0, 1):
        ms = [g["set"][k][0] for g in games]
        sims = [g["set"][k][1] for g in games]
        rate = [s / m * 1000 for m, s in zip(ms, sims) if m > 1000]
        tcap = sum(m >= 19500 for m in ms) / len(ms)
        scap = sum(s >= 19900 for s in sims) / len(sims)
        print(
            f"{acct} settlement {k + 1}: games {len(games)} ms median {med(ms):.0f} "
            f"sims median {med(sims):.0f} (p10 {q(sims, .1)}, p90 {q(sims, .9)}) "
            f"sims/s {med(rate):.0f}; 20 s cap binds {tcap:.0%}, 20k cap {scap:.0%}"
        )
    rms = [r[0] for g in games for r in g["road"]]
    rs = [r[1] for g in games for r in g["road"]]
    print(f"{acct} setup roads: ms median {med(rms):.0f} sims median {med(rs):.0f}")
    think = [g["think"] / 1000 for g in games]
    print(
        f"{acct} bot think time a game: median {med(think):.0f} s, moves {med([g['moves'] for g in games]):.0f}"
    )
    at = [
        json.loads(x)["at"]
        for x in open(f"/home/owen/colonist-ai/data/{acct}/games.jsonl")
        if '"ranked"' in x
    ][-n:]
    gaps = [(b - a) / 60 for a, b in zip(at, at[1:]) if b - a < 3600]
    print(
        f"{acct} ranked games: median gap {med(gaps):.1f} min over {len(gaps)} gaps\n"
    )
