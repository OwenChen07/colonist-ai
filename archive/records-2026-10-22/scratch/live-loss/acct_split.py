"""Per-account ranked win rate by Toronto day and by seat, last N days. Wilson 95%."""

import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
TOR = timezone(timedelta(hours=-4))
now = time.time()


def wilson(w, n):
    if n == 0:
        return (0, 0, 0)
    p = w / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * p, 100 * (c - h), 100 * (c + h))


def fmt(w, n):
    p, lo, hi = wilson(w, n)
    return f"{w}-{n - w} ({p:.0f}% [{lo:.0f},{hi:.0f}])"


for acct in ("bot3", "bot4", "bot5", "bot7"):
    rows = []
    for line in open(f"data/{acct}/games.jsonl"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (
            r.get("queue") != "ranked"
            or "at" not in r
            or r.get("ended") not in ("win", "loss", None)
        ):
            pass
        if r.get("queue") != "ranked" or now - r.get("at", 0) > DAYS * 86400:
            continue
        if r.get("winner") is None:
            continue
        rows.append(r)
    day = defaultdict(lambda: [0, 0])
    seat = defaultdict(lambda: [0, 0])
    rnd = defaultdict(lambda: [0, 0])
    for r in rows:
        won = r["winner"] == r["bot_color"]
        d = datetime.fromtimestamp(r["at"], TOR).strftime("%b %d")
        for tab, k in ((day, d), (seat, r["bot_color"]), (rnd, r.get("round"))):
            tab[k][0] += won
            tab[k][1] += 1
    w = sum(v[0] for v in day.values())
    print(f"{acct}: all {fmt(w, len(rows))}")
    print("  by day: " + "; ".join(f"{k} {fmt(*v)}" for k, v in sorted(day.items())))
    print("  by seat: " + "; ".join(f"{k} {fmt(*v)}" for k, v in sorted(seat.items())))
    print(
        "  by round: "
        + "; ".join(
            f"{k} {fmt(*v)}" for k, v in sorted(rnd.items(), key=lambda x: str(x[0]))
        )
    )
    last = sorted(rows, key=lambda r: r["at"])
    for n in (50, 100, 200):
        s = last[-n:]
        print(
            f"  last {n}: {fmt(sum(r['winner'] == r['bot_color'] for r in s), len(s))}"
        )
