"""E83 account check: bot9 vs bot11 on the same net (step 30), before step 50 started.
If bot9 already beat bot11 by a lot on step 30, the step 50 gap is partly the account.

    .venv/bin/python data/orchestrator/scratch/live-loss/e83_acct.py
"""

from lvlib import compare, load_rows, summary

rows = load_rows()
s30_9 = [r for r in rows if r["a"] == "bot9" and r["label"].startswith("E54 iter30")]
s30_11 = [r for r in rows if r["a"] == "bot11" and r["label"].startswith("E54 iter30")]
s50_9 = [r for r in rows if r["a"] == "bot9" and r["label"].startswith("E54 iter50")]
start50 = s50_9[0]["at"]
pre11 = [r for r in s30_11 if r["at"] < start50]
lo = max(min(r["at"] for r in s30_9), min(r["at"] for r in s30_11))
pre9 = [r for r in s30_9 if r["at"] >= lo]
summary("bot9 step30", pre9)
summary("bot11 step30 pre", pre11)
compare("bot9 - bot11 on step 30 (both before step 50)", pre9, pre11)
post11 = [r for r in s30_11 if r["at"] >= start50]
compare("bot11 step30 after - before", post11, pre11)
compare("bot9 step50 - bot9 step30", s50_9, pre9)
