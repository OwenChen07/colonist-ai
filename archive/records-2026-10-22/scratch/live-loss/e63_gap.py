"""E63 (opponent model on bot7 from Oct 20, 8:39 AM Toronto; bot3 is the
control): bot7 vs bot3 and vs bot3/4/5 since the start, by opponent band, same-band
gap, repeat opponents, 90+ rolls. Plus bot7 vs bot3 before the start (same span
length) as the baseline gap.

    .venv/bin/python data/orchestrator/scratch/live-loss/e63_gap.py
"""

from datetime import datetime

from lvlib import TZ, compare, fmt_t, load_rows, summary, wilson

START = datetime(2026, 10, 20, 8, 39, tzinfo=TZ).timestamp()
rows = load_rows()
after = [r for r in rows if r["at"] >= START]
print(
    f"since {fmt_t(START)}: {len(after)} games"
    + (f", last {fmt_t(after[-1]['at'])}" if after else "")
)
groups = {
    a: [r for r in after if r["a"] == a] for a in ("bot7", "bot3", "bot4", "bot5")
}
groups["bot3/4/5"] = [r for r in after if r["a"] != "bot7"]
for name, sel in groups.items():
    summary(name, sel)
compare("bot7 - bot3 (control)", groups["bot7"], groups["bot3"])
compare("bot7 - bot3/4/5", groups["bot7"], groups["bot3/4/5"])
for name in ("bot7", "bot3"):
    sel = [r for r in groups[name] if r["nth"]]
    first = [r for r in sel if r["nth"] == 1]
    rep = [r for r in sel if r["nth"] >= 2]
    print(
        f"  {name} 1st meeting {wilson(sum(r['won'] for r in first), len(first))}; "
        f"2nd+ {wilson(sum(r['won'] for r in rep), len(rep))}"
    )
print("\nbaseline: last 300 games each before the start")
b7 = [r for r in rows if r["a"] == "bot7" and r["at"] < START][-300:]
b3 = [r for r in rows if r["a"] == "bot3" and r["at"] < START][-300:]
summary("bot7 before", b7)
summary("bot3 before", b3)
compare("bot7 - bot3 before", b7, b3)
