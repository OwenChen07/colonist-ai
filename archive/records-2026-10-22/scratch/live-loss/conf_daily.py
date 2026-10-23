"""Daily over-confidence row (late-game's suggestion, Oct 22, 9:09 PM): the cleaner
measure than 'losses with a 60%+ peak' (that one is mostly built in). Over the last
400 and 1,200 ranked games with a bridge.log curve, for 2300+ opponents and for all:
  gap20 = the bot's stated chance at its 20th turn minus how often it won (games that
           reach turn 20), mean and 95% interval;
  after80 = win rate once the stated chance first reached 80% after setup (Wilson).
An honest bot reads gap20 = 0 and after80 >= 80%. Curves via late-game's peak_story.py
(read-only import). Appends to data/orchestrator/analysis/overconfidence_daily.md.

    .venv/bin/python data/orchestrator/scratch/live-loss/conf_daily.py [--dry]
"""

import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
sys.path.insert(0, "data/orchestrator/scratch/late-game")
from lvlib import ACCOUNTS, TZ, counted, wilson  # noqa: E402
from peak_story import curves_for, per_turn  # noqa: E402

OUT = Path("data/orchestrator/analysis/overconfidence_daily.md")
rc = json.loads(Path("data/orchestrator/analysis/opponent_ratings.json").read_text())
oc = json.loads(Path("data/orchestrator/analysis/opponent_hash.json").read_text())

games = []
no_mmr = 0
for acc in ACCOUNTS:
    p = Path(f"data/{acc}/games.jsonl")
    if not p.exists():
        continue
    cv = curves_for(acc)
    for line in open(p):
        if not line.strip():
            continue
        g = json.loads(line)
        if (
            g.get("queue") != "ranked"
            or g.get("own_bot")
            or g.get("winner") is None
            or not counted(acc, g)
        ):
            continue
        mv = cv.get(g["capture"])
        pts = per_turn(mv) if mv else []
        if not pts:
            continue
        mmr = (oc.get(g["capture"]) or {}).get("opp_mmr") or (
            rc.get(g["capture"]) or {}
        ).get("opp_mmr")
        no_mmr += mmr is None
        t20 = [q for k, q in pts if k == 20]
        f80 = any(q >= 80 for k, q in pts)
        games.append(
            (g["at"], mmr, g["winner"] == g["bot_color"], t20[0] if t20 else None, f80)
        )
games.sort()


def gap(sel):
    d = [q / 100 - w for _, _, w, q, _ in sel if q is not None]
    if len(d) < 2:
        return "n/a"
    m = sum(d) / len(d)
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1))
    h = 1.96 * sd / math.sqrt(len(d))
    return f"{100 * m:+.0f} [{100 * (m - h):+.0f}, {100 * (m + h):+.0f}] (n {len(d)})"


def after80(sel):
    s = [w for _, _, w, _, f in sel if f]
    return wilson(sum(s), len(s))


def cells(n):
    sel = games[-n:]
    hi = [g for g in sel if g[1] is not None and g[1] >= 2300]
    return [gap(hi), after80(hi), gap(sel), after80(sel)], sel


c4, s4 = cells(400)
c12, s12 = cells(1200)
span = (
    lambda s: f"{datetime.fromtimestamp(s[0][0], TZ):%b %d %-I:%M %p} to {datetime.fromtimestamp(s[-1][0], TZ):%b %d %-I:%M %p}"
)
now = datetime.now(TZ)
row = (
    f"| {now:%b %d, %-I:%M %p} | "
    + " | ".join(c4)
    + " | "
    + " | ".join(c12)
    + f" | {span(s4)} | {span(s12)} |"
)
header = (
    "# Over-confidence, daily\n\n"
    "One row a day with the opponent-rating row. Ranked games on every counted account (lvlib), with the "
    "live search's stated win chance from bridge.log. gap20: stated chance at the bot's 20th turn minus how "
    "often it won, in points (honest = 0). after80: win rate once the bot first said 80% (honest = 80% or more). "
    "Brackets are 95% intervals. Suggested by late-game, Oct 22, 9:09 PM, in place of 'losses with a 60%+ peak'.\n\n"
    "| Written | 400: gap20 2300+ | 400: after80 2300+ | 400: gap20 all | 400: after80 all "
    "| 1200: gap20 2300+ | 1200: after80 2300+ | 1200: gap20 all | 1200: after80 all | 400 span | 1200 span |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n"
)
print(f"{len(games)} games with curves, {no_mmr} without a cached rating")
print(row)
if "--dry" not in sys.argv:
    text = OUT.read_text() if OUT.exists() else header
    OUT.write_text(text + row + "\n")
    print("appended to", OUT)
