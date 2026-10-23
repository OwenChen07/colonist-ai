"""Longest-road metrics per net round for training's live judgement of a promoted
round: share of games where the bot takes LR first, and the bot's win rate when
the person takes it first. Joins data/<account>/games.jsonl (round) with
data/human/index.jsonl (bot_won, resignation-safe) and replays each capture once;
results cached in live-loss/lr_first_cache.json so later runs only replay new
games. Usage: lr_by_round.py [min_round] [workers]. Aggregates only, no names."""

import json
import os
import sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))
from road_race import facts  # noqa: E402

from catanatron.colonist.loss_report import wilson  # noqa: E402

MIN_ROUND = int(sys.argv[1]) if len(sys.argv) > 1 else 56
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
CACHE = "data/orchestrator/live-loss/lr_first_cache.json"


def first_holder(job):
    account, cap = job
    try:
        o = facts(os.path.join("captures", account, cap))
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        o = None
    if o is None:
        return cap, "skip"
    return cap, o.first[0] if o.first else "nobody"


def main():
    with open("data/human/index.jsonl") as f:
        idx = [json.loads(line) for line in f if line.strip()]
    idx = {r["capture"]: r for r in idx if r.get("status") == "ok"}
    games = {}
    for a in ACCOUNTS:
        with open(f"data/{a}/games.jsonl") as f:
            for line in f:
                if not line.strip():
                    continue
                g = json.loads(line)
                if g.get("queue") != "ranked" or g.get("own_bot"):
                    continue
                if (g.get("round") or -1) < MIN_ROUND or g["capture"] not in idx:
                    continue
                games[g["capture"]] = (a, g["round"])
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            cache = json.load(f)
    todo = [(a, c) for c, (a, _) in games.items() if c not in cache]
    print(f"games {len(games)}, cached {len(games) - len(todo)}, replaying {len(todo)}")
    if todo:
        with Pool(WORKERS) as pool:
            for cap, h in pool.imap_unordered(first_holder, todo, chunksize=8):
                cache[cap] = h
        with open(CACHE, "w") as f:
            json.dump(cache, f)
    by = defaultdict(lambda: defaultdict(list))
    for c, (a, rnd) in games.items():
        by[rnd][cache[c]].append(idx[c]["bot_won"])
    print("round  n    bot-first share        win|person-first        win overall")
    for rnd in sorted(by):
        d = by[rnd]
        n = sum(len(v) for k, v in d.items() if k != "skip")
        bf, pf = d["bot"], d["person"]
        allw = [w for k, v in d.items() if k != "skip" for w in v]
        print(
            f"{rnd:>5} {n:>4}  {wilson(len(bf), n):>22}  {wilson(sum(pf), len(pf)):>22}"
            f"  {wilson(sum(allw), len(allw)):>22}  (skip {len(d['skip'])})"
            f"\n       bot-first among claimed {wilson(len(bf), len(bf) + len(pf))}"
        )


if __name__ == "__main__":
    main()
