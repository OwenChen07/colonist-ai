"""E71: paired differences between arms by start, with 95% ranges, plus
agreement with the live result from the same start."""

import json
import math
import sys

OUT = "/home/owen/colonist-ai/data/orchestrator/e71"
ARMS = ["r73-vs-people", "r73-vs-r73", "r152-vs-people", "e54-vs-people"]
PREFIX = sys.argv[1] if len(sys.argv) > 1 else ""  # "mid-" for step 1


def load(name):
    rows = {}
    for line in open(f"{OUT}/{PREFIX}{name}.jsonl"):
        r = json.loads(line)
        rows[r["seed"]] = r
    return rows


def mci(xs):
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m, 1.96 * sd / math.sqrt(len(xs))


def band(r):
    mmr = r.get("opp_mmr")
    if mmr is None:
        return "no rating"
    return "2100+" if mmr >= 2100 else "<2100"


data = {a: load(a) for a in ARMS}
seeds = sorted(set.intersection(*(set(d) for d in data.values())))
print(f"starts in all four arms: {len(seeds)}")
for a in ARMS:
    d = data[a]
    same = [float((d[s]["score"] > 0.5) == bool(d[s]["bot_won"])) for s in seeds]
    m, h = mci(same)
    print(f"{a}: same result as live {m:.1%} ± {h:.1%}")
for base, other in [
    ("r73-vs-people", "r152-vs-people"),
    ("r73-vs-people", "e54-vs-people"),
    ("r73-vs-r73", "r73-vs-people"),
]:
    for name, keep in [
        ("all", lambda r: True),
        ("2100+", lambda r: band(r) == "2100+"),
    ]:
        xs = [
            data[base][s]["score"] - data[other][s]["score"]
            for s in seeds
            if keep(data[base][s])
        ]
        m, h = mci(xs)
        print(f"{base} minus {other} [{name}]: {m:+.1%} ± {h:.1%} (n {len(xs)})")
# Comebacks: starts the person went on to win live; how often does each arm win?
for name, want in [("person won live", False), ("bot won live", True)]:
    for a in ARMS:
        xs = [data[a][s]["score"] for s in seeds if bool(data[a][s]["bot_won"]) == want]
        m, h = mci(xs)
        print(f"{name}: {a} net {m:.1%} ± {h:.1%} (n {len(xs)})")
