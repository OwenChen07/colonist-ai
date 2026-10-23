"""Losses from ahead: in losses, the bot's peak win chance (from bridge.log), target vs 2300+ band."""

import json
import re
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import wilson  # noqa: E402
from phantom import OUT  # noqa: E402

PAT = re.compile(r"win chance (\d+)%")
stats = json.loads(OUT.read_text())
games = {}
for a in (
    "ranked",
    "bot2",
    "bot3",
    "bot4",
    "bot5",
    "bot6",
    "bot7",
    "bot8",
    "bot9",
    "bot10",
    "bot11",
    "bot12",
):
    try:
        for line in open(f"data/{a}/games.jsonl"):
            if line.strip():
                g = json.loads(line)
                games[f"{a}/{g['capture']}"] = g
    except FileNotFoundError:
        pass
want = {k for k, s in stats.items() if s}
curves = {}
for a in sorted({k.split("/")[0] for k in want}):
    moves = []
    try:
        f = open(f"data/{a}/bridge.log", errors="replace")
    except FileNotFoundError:
        continue
    for line in f:
        if "game started" in line:
            moves = []
        m = PAT.search(line)
        if m and "move:" in line:
            moves.append(int(m.group(1)))
        if "game saved to" in line:
            key = f"{a}/{line.rstrip().rsplit('/', 1)[-1]}"
            if key in want and moves:
                curves[key] = moves
print(f"curves {len(curves)} of {len(want)}")
for label, sel in (
    ("target", lambda s: s["target"]),
    ("band", lambda s: not s["target"]),
):
    losses = [
        k
        for k, s in stats.items()
        if s and sel(s) and k in curves and games[k]["winner"] != games[k]["bot_color"]
    ]
    wins = [
        k
        for k, s in stats.items()
        if s and sel(s) and k in curves and games[k]["winner"] == games[k]["bot_color"]
    ]
    for t in (60, 75):
        print(
            f"  {label} losses with peak >= {t}: {wilson(sum(max(curves[k]) >= t for k in losses), len(losses))}"
        )
    print(
        f"  {label} wins with low <= 40: {wilson(sum(min(curves[k][4:] or [50]) <= 40 for k in wins), len(wins))}"
    )
    pk = sorted(max(curves[k]) for k in losses)
    if pk:
        print(f"  {label} loss peak median {pk[len(pk) // 2]}")
