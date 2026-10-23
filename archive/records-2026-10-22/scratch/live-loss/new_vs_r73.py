"""Rounds 150+ vs round 73 live: per account and per round, against r73's last
300 ranked games per account. bot4/5/7 retired Oct 20, 9:47 AM; bot9 is new
(no r73 games), so its games count in the new pool but not in the gap.

    .venv/bin/python data/orchestrator/scratch/live-loss/new_vs_r73.py
"""

from collections import defaultdict

from lvlib import ACCOUNTS, compare, fmt_t, load_rows, summary

OLD = 73
rows = load_rows()
new = [r for r in rows if (r["round"] or 0) >= 150]
by_round = defaultdict(list)
for r in new:
    by_round[r["round"]].append(r)
for k in sorted(by_round):
    g = by_round[k]
    print(f"\n=== round {k}: {fmt_t(g[0]['at'])} .. {fmt_t(g[-1]['at'])}")
    summary(f"r{k}", g)
tot_new, tot_old = [], []
for a in ACCOUNTS:
    n = [r for r in new if r["a"] == a]
    # bot3 back on r73 from Oct 20, 3:33 PM: keep its baseline before the new
    # nets and show the pinned games apart, as a same-hours control.
    first_new = n[0]["at"] if n else float("inf")
    o_all = [r for r in rows if r["a"] == a and r["round"] == OLD]
    o = [r for r in o_all if r["at"] < first_new][-300:]
    pinned = [r for r in o_all if r["at"] > first_new]
    print(f"\n=== {a}")
    summary("r150+", n)
    if pinned:
        summary(f"r{OLD} pinned", pinned)
    if o:
        summary(f"r{OLD} last 300", o)
        compare(f"r150+ - r{OLD}", n, o)
        tot_new += n
        tot_old += o
print("\n=== accounts with an r73 baseline")
summary("r150+", tot_new)
summary(f"r{OLD} last 300", tot_old)
compare(f"r150+ - r{OLD}", tot_new, tot_old)
