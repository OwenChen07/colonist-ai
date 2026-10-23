"""E88 read: bot's chair win rate vs people model minus vs itself, paired by seed."""

import json
import math
import sys

D = "data/orchestrator/e88/"


def load(name):
    with open(D + name) as f:
        return {r["seed"]: r for r in map(json.loads, f)}


def main():
    a = load("s50-vs-people.jsonl")
    b = load("s50-vs-s50.jsonl")
    seeds = sorted(set(a) & set(b))
    print(f"paired starts: {len(seeds)} (people {len(a)}, self {len(b)})")
    bands = {
        "all": lambda m: True,
        "<2100": lambda m: m is not None and m < 2100,
        "2100+": lambda m: m is not None and m >= 2100,
        "2300+": lambda m: m is not None and m >= 2300,
        "no mmr": lambda m: m is None,
    }
    for name, keep in bands.items():
        rows = [
            (a[s]["score"], b[s]["score"], a[s]["bot_won"])
            for s in seeds
            if keep(a[s]["opp_mmr"])
        ]
        n = len(rows)
        if n < 2:
            continue
        pa = sum(r[0] for r in rows) / n
        pb = sum(r[1] for r in rows) / n
        live = sum(r[2] for r in rows) / n
        d = [r[0] - r[1] for r in rows]
        md = sum(d) / n
        sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1))
        h = 1.96 * sd / math.sqrt(n)
        print(
            f"{name:7s} n {n:4d}  vs people {100*pa:5.1f}  vs self {100*pb:5.1f}  live {100*live:5.1f}"
            f"  people-self {100*md:+5.1f} [{100*(md-h):+5.1f},{100*(md+h):+5.1f}]"
        )


if __name__ == "__main__":
    main()
