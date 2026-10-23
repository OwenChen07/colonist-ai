"""E78 band-matched read: bot8 (robber guard) vs bot12 (control) from START epoch.

.venv/bin/python data/orchestrator/scratch/live-loss/e78_band.py START
"""

import sys

from lvlib import compare, load_rows, summary

START = float(sys.argv[1])
rows = load_rows()
arm = [r for r in rows if r["a"] == "bot8" and r["at"] >= START]
ctrl = [r for r in rows if r["a"] == "bot12" and r["at"] >= START]


def expected(sel):
    rated = [r for r in sel if r["opp_mmr"] is not None and r["bot_mmr"] is not None]
    if not rated:
        return "no ratings"
    e = sum(1 / (1 + 10 ** ((r["opp_mmr"] - r["bot_mmr"]) / 400)) for r in rated) / len(
        rated
    )
    w = sum(r["won"] for r in rated) / len(rated)
    om = sum(r["opp_mmr"] for r in rated) / len(rated)
    bm = sum(r["bot_mmr"] for r in rated) / len(rated)
    return f"actual {w:.1%} vs expected {e:.1%} ({100 * (w - e):+.1f} pts, {len(rated)} rated, opp {om:.0f}, bot {bm:.0f})"


# bot8 before the guard: best-net games from Oct 21, 10:16 AM (lvlib drops its people-model games)
base = [r for r in rows if r["a"] == "bot8" and r["at"] < START]
for name, sel in (("E78 bot8", arm), ("ctrl bot12", ctrl), ("bot8 before", base)):
    summary(name, sel)
    print(f"                {expected(sel)}")
compare("bot8 - bot12", arm, ctrl)
compare("bot8 - bot8 before", arm, base)
