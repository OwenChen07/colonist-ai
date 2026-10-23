"""E14 live metric: round 73 vs round 66 on the same accounts. Ranked record per
account and round (with the Toronto time span), r66 in full and in its last 24 h
on each account, plus dead roads and roads before the 3rd settlement per game from
data/human/index.jsonl. Aggregates only, no names."""

import json
import math
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from catanatron.colonist.loss_report import wilson

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
ROUNDS = (66, 73)
TZ = ZoneInfo("America/Toronto")

idx = {}
with open("data/human/index.jsonl") as f:
    for line in f:
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok":
                idx[r["capture"]] = r

rows = defaultdict(list)  # (acct, round) -> [(at, won, idx row or None)]
for a in ACCOUNTS:
    with open(f"data/{a}/games.jsonl") as f:
        for line in f:
            if not line.strip():
                continue
            g = json.loads(line)
            if (
                g.get("queue") != "ranked"
                or g.get("own_bot")
                or g.get("round") not in ROUNDS
            ):
                continue
            ix = idx.get(g["capture"])
            won = (
                ix["bot_won"]
                if ix and "bot_won" in ix
                else g.get("winner") == g.get("bot_color")
            )
            rows[(a, g["round"])].append((g["at"], bool(won), ix))


def t(x):
    return datetime.fromtimestamp(x, TZ).strftime("%b %d %I:%M %p")


def mean_ci(xs):
    n = len(xs)
    if n < 2:
        return "n<2"
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return f"{m:.2f} ± {1.96 * sd / math.sqrt(n):.2f} (n {n})"


def line(label, g):
    if not g:
        print(f"  {label}: none")
        return
    w = sum(1 for _, won, _ in g if won)
    print(
        f"  {label}: {w}-{len(g) - w}  {wilson(w, len(g))}  {t(min(x[0] for x in g))} .. {t(max(x[0] for x in g))}"
    )


tot = defaultdict(list)
for a in ACCOUNTS:
    print(a)
    for rnd in ROUNDS:
        g = sorted(rows[(a, rnd)])
        line(f"r{rnd} all", g)
        tot[f"r{rnd} all"] += g
        if rnd == 66 and g:
            last = [x for x in g if x[0] >= g[-1][0] - 86400]
            line("r66 last 24h", last)
            tot["r66 last 24h"] += last
print("all accounts")
for k, g in tot.items():
    line(k, g)

print("roads (index rows with bot stats)")
for k in ("r66 all", "r66 last 24h", "r73 all"):
    st = [x[2]["bot"] for x in tot[k] if x[2] and "bot" in x[2]]
    print(
        f"  {k}: dead {mean_ci([s['dead_roads'] for s in st])}  early {mean_ci([s['early_roads'] for s in st])}"
    )
