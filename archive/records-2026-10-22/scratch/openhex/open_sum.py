"""Summarize an open_study.py log (JSON rows): python open_sum.py LOG"""

import json
import sys

import numpy as np

rows = [json.loads(l) for l in open(sys.argv[1]) if l.startswith("{")]
n = len(rows)
gap = np.array([r["q_best"] - r["q_played"] for r in rows])
won = np.array([r["won"] for r in rows])
same = np.array([r["same"] for r in rows])
print(f"n={n} won={won.sum()} ({won.mean():.0%})")
print(
    f"played == big search: {same.sum()}/{n}; won when same {won[same].sum()}/{same.sum()}, "
    f"won when different {won[~same].sum()}/{(~same).sum()}"
)
print(
    f"q gap: mean {gap.mean():.3f}, median {np.median(gap):.3f}, max {gap.max():.3f}, "
    f"gap>0.05: {(gap > 0.05).sum()}, gap>0.10: {(gap > 0.10).sum()}"
)
d = gap[~same]
if len(d):
    print(f"q gap when different: mean {d.mean():.3f} over {len(d)}")
print(
    f"net value at our first pick: mean {np.mean([r['v'] for r in rows]):+.3f}; "
    f"wins {np.mean([r['v'] for r in rows if r['won']]):+.3f}, "
    f"losses {np.mean([r['v'] for r in rows if not r['won']]):+.3f}"
)
print(f"q_played mean {np.mean([r['q_played'] for r in rows]):+.3f}")
print(
    f"pips played {np.mean([r['pips_played'] for r in rows]):.2f}, big {np.mean([r['pips_best'] for r in rows]):.2f}"
)
print(
    f"prior rank of played: "
    + str({k: sum(r["prior_rank"] == k for r in rows) for k in range(1, 8)})
)
print(
    f"rerun at live budget same as played: {sum(r['rerun_same'] for r in rows)}/{n}; "
    f"same as big: {sum(r['rerun_vs_big'] for r in rows)}/{n}"
)
ls = [r["live_sims"] for r in rows if r["live_sims"]]
print(
    f"live sims: median {np.median(ls):.0f}, min {min(ls)}, under 5000: {sum(x < 5000 for x in ls)}"
)
print(f"sec per row mean {np.mean([r['sec'] for r in rows]):.0f}")
