"""Quit rate and finished-only win rate per account and arm, from games.jsonl's 'ended'
field (win / resignation). Ranked games only. Usage: quit_rate.py"""

import json
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ACCOUNTS = {
    "bot3": "Dilly6283",
    "bot10": "Finlay3621",
    "bot9": "Tedi4960",
    "bot11": "fymouushii",
    "bot12": "wifedat",
    "bot8": "Vasos5306",
}
TZ = ZoneInfo("America/Toronto")
E80 = datetime(2026, 10, 21, 10, 26, tzinfo=TZ).timestamp()
E54 = datetime(2026, 10, 20, 20, 41, tzinfo=TZ).timestamp()
NOW = time.time()


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def load(a):
    rows = []
    for line in open(f"data/{a}/games.jsonl"):
        r = json.loads(line)
        if r.get("queue", "ranked") != "ranked" or "ended" not in r:
            continue
        r["won"] = r["winner"] == r["bot_color"]
        rows.append(r)
    return rows


def line(name, rows):
    n = len(rows)
    if n == 0:
        print(f"{name:40s} n=0")
        return
    w = sum(r["won"] for r in rows)
    oq = sum(r["won"] and r["ended"] == "resignation" for r in rows)
    bq = sum((not r["won"]) and r["ended"] == "resignation" for r in rows)
    fin = [r for r in rows if r["ended"] != "resignation"]
    fw = sum(r["won"] for r in fin)
    lo, hi = wilson(oq, n)
    flo, fhi = wilson(fw, len(fin))
    print(
        f"{name:40s} n={n:4d} won {w:4d} ({100*w/n:4.1f}%) | opp quit {oq:3d} "
        f"({100*oq/n:4.1f}% [{lo:4.1f},{hi:4.1f}]) bot-side resign {bq} | finished "
        f"{len(fin):4d} won {fw:4d} ({100*fw/max(1,len(fin)):4.1f}% [{flo:4.1f},{fhi:4.1f}])"
    )


def main():
    data = {a: load(a) for a in ACCOUNTS}
    print("== since Oct 21, 10:26 AM (E80 start)")
    for a in ("bot3", "bot10"):
        line(f"{ACCOUNTS[a]} ({a})", [r for r in data[a] if r["at"] >= E80])
    print("== last 24 h")
    for a, nm in ACCOUNTS.items():
        line(f"{nm} ({a})", [r for r in data[a] if r["at"] >= NOW - 86400])
    print("== all time")
    for a, nm in ACCOUNTS.items():
        line(f"{nm} ({a})", data[a])
    print("== E54 arms vs round 152 history")
    b9 = data["bot9"]
    line("Tedi4960 E54", [r for r in b9 if r["label"].startswith("E54")])
    line("fymouushii E54", [r for r in data["bot11"] if r["label"].startswith("E54")])
    line(
        "Tedi4960 round 152 before E54",
        [r for r in b9 if r.get("round") == 152 and r["at"] < E54],
    )
    line(
        "bot8 best net (non-152, last 24h)",
        [r for r in data["bot8"] if r["at"] >= NOW - 86400 and r.get("round") != 152],
    )
    print("== by net round, all accounts pooled (n>=50)")
    by = {}
    for a in ACCOUNTS:
        for r in data[a]:
            by.setdefault(r.get("round"), []).append(r)
    for k in sorted(by, key=lambda x: (x is None, x)):
        if len(by[k]) >= 50:
            line(f"round {k}", by[k])


main()
