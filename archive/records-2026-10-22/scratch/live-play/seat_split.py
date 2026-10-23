"""Seat split over the last N ranked games per account, and the first
settlement's search by seat. Seat from the capture's first full snapshot
(currentTurnPlayerColor == playerColor: first seat)."""

import base64
import json
import os
import re
import sys
from collections import defaultdict

import msgpack

ROOT = os.path.expanduser("~/colonist-ai")
ACCOUNTS = ["bot3", "bot4", "bot5", "bot7"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 250
MOVE = re.compile(r"  move: (.*) \(win chance \d+%, (\d+) ms(?:, (\d+) sims)?\)")
SAVED = re.compile(r"game saved to (\S+)")


def first_settlement(path):
    out, first = {}, None
    with open(path, errors="replace") as f:
        for line in f:
            if "following: game started" in line:
                first = None
            m = MOVE.match(line.rstrip("\n"))
            if m and first is None and m.group(1).startswith("settlement"):
                first = (int(m.group(2)), int(m.group(3) or -1))
            s = SAVED.search(line)
            if s and first:
                out[s.group(1).rsplit("/", 1)[-1]] = first
    return out


def seat(path):
    doc = json.load(open(path))
    for m in doc["messages"]:
        if m.get("dir") != "in" or "b64" not in m:
            continue
        try:
            d = msgpack.unpackb(
                base64.b64decode(m["b64"]), raw=False, strict_map_key=False
            )
        except Exception:
            continue
        p = (d.get("data") or {}).get("payload") if isinstance(d, dict) else None
        if isinstance(p, dict) and isinstance(p.get("gameState"), dict):
            cs = p["gameState"].get("currentState") or {}
            return 1 if cs.get("currentTurnPlayerColor") == p.get("playerColor") else 2
    return None


stats = defaultdict(lambda: [0, 0])  # (acct, seat) -> wins, games
fs = defaultdict(list)  # seat -> (ms, sims)
for acct in ACCOUNTS:
    firsts = first_settlement(f"{ROOT}/data/{acct}/bridge.log")
    recs = []
    for line in open(f"{ROOT}/data/{acct}/games.jsonl"):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if g.get("queue") == "ranked" and g.get("winner"):
            recs.append(g)
    for g in recs[-N:]:
        path = f"{ROOT}/captures/{acct}/{g['capture']}"
        if not os.path.exists(path):
            continue
        s = seat(path)
        if s is None:
            continue
        stats[(acct, s)][1] += 1
        stats[(acct, s)][0] += g["winner"] == g["bot_color"]
        if g["capture"] in firsts:
            fs[s].append(firsts[g["capture"]])
            if s == 1 and len(sys.argv) > 2 and g["capture"] >= "bridge-" + sys.argv[2]:
                print("  first seat", acct, g["capture"], firsts[g["capture"]])
tot = defaultdict(lambda: [0, 0])
for (acct, s), (w, n) in sorted(stats.items()):
    print(f"{acct} seat {s}: {w}-{n - w} ({w / n:.1%})")
    tot[s][0] += w
    tot[s][1] += n
for s, (w, n) in sorted(tot.items()):
    print(f"ALL seat {s}: {w}-{n - w} ({w / n:.1%}, ±{1.96 * (0.25 / n) ** 0.5:.1%})")
for s, xs in sorted(fs.items()):
    xs.sort()
    ms = [x[0] for x in xs]
    sims = sorted(x[1] for x in xs)
    print(
        f"first settlement, seat {s}: {len(xs)} games, median {ms[len(ms) // 2]} ms, "
        f"median sims {sims[len(sims) // 2]}, at 2400 sims or fewer {sum(x <= 2400 for x in sims)}"
    )
