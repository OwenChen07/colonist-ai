"""E61 rerun on Dilly6283 (bot3): round 73 games since the E61 start, by band, against
bot3's round 73 baseline (last 300 games before the new nets, 62.1%) and against
colonist's expected score. Harm stop (lead, Oct 20, 8:45 PM): from 75 games, under
50%, or 7+ points under the baseline with the 95% range excluding zero.

    .venv/bin/python data/orchestrator/scratch/live-loss/e61_rerun.py START_UTC_ISO
"""

import math
import sys
from datetime import datetime, timezone

from lvlib import compare, fmt_t, load_rows, summary

START = datetime.fromisoformat(sys.argv[1]).replace(tzinfo=timezone.utc).timestamp()
rows = [r for r in load_rows() if r["a"] == "bot3"]
first_new = min(
    (r["at"] for r in rows if (r["round"] or 0) >= 150), default=float("inf")
)
r73 = [r for r in rows if r["round"] == 73]
base = [r for r in r73 if r["at"] < first_new][-300:]
pinned = [r for r in r73 if first_new < r["at"] < START]
arm = [r for r in r73 if r["at"] >= START]
other = [r for r in rows if r["at"] >= START and r["round"] != 73]


def expected(sel):
    rated = [r for r in sel if r["opp_mmr"] is not None and r["bot_mmr"] is not None]
    if not rated:
        return "no ratings"
    e = sum(1 / (1 + 10 ** ((r["opp_mmr"] - r["bot_mmr"]) / 400)) for r in rated) / len(
        rated
    )
    w = sum(r["won"] for r in rated) / len(rated)
    return f"actual {w:.1%} vs expected {e:.1%} ({100 * (w - e):+.1f} pts, {len(rated)} rated)"


print(
    f"E61 start {fmt_t(START)}; arm games {len(arm)}"
    + (f", last {fmt_t(arm[-1]['at'])}" if arm else "")
)
if other:
    print(f"WARNING: {len(other)} games since the start not on round 73")
for name, sel in (("E61 arm", arm), ("r73 pinned", pinned), ("r73 last 300", base)):
    if sel:
        summary(name, sel)
        print(f"                {expected(sel)}")
compare("arm - last 300", arm, base)
compare("arm - pinned", arm, pinned)
if arm:
    n, w = len(arm), sum(r["won"] for r in arm)
    p = w / n
    pb = sum(r["won"] for r in base) / len(base)
    se = math.sqrt(p * (1 - p) / n + pb * (1 - pb) / len(base))
    d = p - pb
    harm = n >= 75 and (p < 0.5 or (d <= -0.07 and d + 1.96 * se < 0))
    print(
        f"harm check: {n} games, {p:.1%}, raw gap {100 * d:+.1f} [{100 * (d - 1.96 * se):+.1f}, "
        f"{100 * (d + 1.96 * se):+.1f}] -> {'HARM STOP' if harm else 'ok' if n >= 75 else 'before 75 games'}"
    )
