"""E79 paired result: good-hex arm minus bad-hex (live) arm, same position and
dice line by line; 95% range by bootstrap over the positions' games (captures).

    .venv/bin/python data/orchestrator/scratch/research/e79_pair.py NAME

reads data/orchestrator/e79/NAME_good.jsonl and NAME_bad.jsonl (e71_late.py
output, run with --starts ../e79/starts_good / starts_bad).
"""

import json
import random
import sys

D = "data/orchestrator/e79"


def main():
    name = sys.argv[1]
    good = [json.loads(x) for x in open(f"{D}/{name}_good.jsonl")]
    bad = [json.loads(x) for x in open(f"{D}/{name}_bad.jsonl")]
    by = {}
    for g, b in zip(good, bad):
        assert g["capture"] == b["capture"]
        by.setdefault(g["capture"], []).append((g["score"], b["score"]))
    groups = list(by.values())

    def stat(gs):
        n = sum(len(x) for x in gs)
        return (
            sum(a for x in gs for a, _ in x) / n,
            sum(b for x in gs for _, b in x) / n,
            sum(a - b for x in gs for a, b in x) / n,
        )

    g, b, d = stat(groups)
    rng = random.Random(79)
    ds = sorted(stat(rng.choices(groups, k=len(groups)))[2] for _ in range(4000))
    n = sum(map(len, groups))
    print(f"{name}: {n} pairs from {len(groups)} positions")
    print(f"  good hex {g:.1%}, bad hex (live) {b:.1%}")
    print(f"  good minus bad {d:+.1%} [{ds[100]:+.1%}, {ds[3899]:+.1%}]")


if __name__ == "__main__":
    main()
