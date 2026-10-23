"""Round 150 vs round 73 live (lead, Oct 20, 8:42 AM): per account, r150 all
against r73's last 300 ranked games, by opponent band, same-band gap, 90+ rolls.
bot7's r150 games are also E63 (opponent model from Oct 20, 8:39 AM).

    .venv/bin/python data/orchestrator/scratch/live-loss/r150_vs_r73.py
"""

from lvlib import ACCOUNTS, compare, fmt_t, load_rows, summary

NEW, OLD = 150, 73
rows = load_rows()
tot_new, tot_old = [], []
for a in ACCOUNTS:
    new = [r for r in rows if r["a"] == a and r["round"] == NEW]
    old = [r for r in rows if r["a"] == a and r["round"] == OLD][-300:]
    span = f"{fmt_t(new[0]['at'])} .. {fmt_t(new[-1]['at'])}" if new else "none yet"
    print(f"\n=== {a}  r{NEW}: {span}" + ("  (also E63)" if a == "bot7" else ""))
    summary(f"r{NEW}", new)
    summary(f"r{OLD} last 300", old)
    compare(f"r{NEW} - r{OLD}", new, old)
    tot_new += new
    tot_old += old
print("\n=== all four accounts")
summary(f"r{NEW}", tot_new)
summary(f"r{OLD} last 300", tot_old)
compare(f"r{NEW} - r{OLD}", tot_new, tot_old)
x = [r for r in tot_new if r["a"] != "bot7"]
y = [r for r in tot_old if r["a"] != "bot7"]
compare(f"without bot7 (E63)", x, y)
