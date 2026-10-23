"""Calibration of the bot's stated win chance by game phase, ranked games.
Phase = the bot's own roll count so far (total rolls ~ 2x). Only searched
moves (sims > 0). Args: N (last N ranked games per account)."""

import json
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.expanduser("~/colonist-ai")
ACCOUNTS = (
    sys.argv[3].split(",") if len(sys.argv) > 3 else ["bot3", "bot4", "bot5", "bot7"]
)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
TAIL = int(sys.argv[2]) if len(sys.argv) > 2 else 0  # drop the last TAIL bot rolls
MOVE = re.compile(r"  move: (.*) \(win chance (\d+)%, (\d+) ms(?:, (\d+) sims)?\)")
SAVED = re.compile(r"game saved to (\S+)")


def games_from_log(path):
    out, cur = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            if "following: game started" in line:
                cur = []
            m = MOVE.match(line.rstrip("\n"))
            if m:
                cur.append((m.group(1), int(m.group(2)) / 100, int(m.group(4) or 0)))
            s = SAVED.search(line)
            if s and cur:
                name = s.group(1).rsplit("/", 1)[-1]
                out.setdefault(name, cur)
    return out


PH = [(0, 15), (15, 30), (30, 45), (45, 999)]
CB = [0, 0.2, 0.4, 0.6, 0.8, 1.01]
cal = defaultdict(lambda: [0, 0.0, 0])  # (phase, cbin) -> n, sum p, wins
brier = defaultdict(lambda: [0, 0.0, 0.0])  # phase -> n, sum (p-z)^2, sum (0.5-z)^2
length = defaultdict(lambda: [0, 0])  # final phase -> wins, games
lead_lost = defaultdict(lambda: [0, 0])  # phase of first >=80% -> games, lost
for acct in ACCOUNTS:
    logs = games_from_log(f"{ROOT}/data/{acct}/bridge.log")
    recs = []
    for line in open(f"{ROOT}/data/{acct}/games.jsonl"):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if g.get("queue") == "ranked" and g.get("winner") and g.get("round") == 73:
            recs.append(g)
    for g in recs[-N:]:
        moves = logs.get(g["capture"])
        if not moves:
            continue
        z = 1.0 if g["winner"] == g["bot_color"] else 0.0
        total = sum(name == "roll" for name, _, _ in moves)
        rolls, per_game, first_hi = 0, defaultdict(list), None
        for name, p, sims in moves:
            if name == "roll":
                rolls += 1
            if sims <= 0 or rolls > total - TAIL:
                continue
            ph = next(i for i, (a, b) in enumerate(PH) if a <= rolls < b)
            per_game[ph].append(p)
            if p >= 0.8 and first_hi is None and rolls >= 10:
                first_hi = ph
        fp = next(i for i, (a, b) in enumerate(PH) if a <= rolls < b)
        length[fp][0] += z
        length[fp][1] += 1
        if first_hi is not None:
            lead_lost[first_hi][0] += 1
            lead_lost[first_hi][1] += z == 0
        # one weight per game per phase: the mean stated chance in that phase
        for ph, ps in per_game.items():
            p = sum(ps) / len(ps)
            cb = next(i for i in range(5) if CB[i] <= p < CB[i + 1])
            c = cal[(ph, cb)]
            c[0] += 1
            c[1] += p
            c[2] += z
            b = brier[ph]
            b[0] += 1
            b[1] += (p - z) ** 2
            b[2] += (0.5 - z) ** 2

lab = lambda ph: (
    f"bot rolls {PH[ph][0]}-{PH[ph][1] - 1}"
    if PH[ph][1] < 999
    else f"bot rolls {PH[ph][0]}+"
)
print("final length (bot rolls; total rolls ~2x): record")
for ph in range(4):
    w, n = length[ph]
    if n:
        print(f"  {lab(ph)}: {int(w)}-{n - int(w)} ({w / n:.1%}, n={n})")
print("calibration, per game and phase (mean stated chance in that phase):")
for ph in range(4):
    b = brier[ph]
    if not b[0]:
        continue
    print(
        f"  {lab(ph)}: n={b[0]}, value MSE {b[1] / b[0]:.3f} (coin flip {b[2] / b[0]:.3f})"
    )
    for cb in range(5):
        n, sp, w = cal[(ph, cb)]
        if n:
            print(
                f"    stated {CB[cb]:.0%}-{min(CB[cb + 1], 1):.0%}: n={n}, stated {sp / n:.1%}, won {w / n:.1%}, gap {sp / n - w / n:+.1%}"
            )
print("first searched move at >=80% (after 10 bot rolls): games, lost")
for ph in range(4):
    n, lost = lead_lost[ph]
    if n:
        print(f"  {lab(ph)}: {n} games, lost {lost} ({lost / n:.1%})")
