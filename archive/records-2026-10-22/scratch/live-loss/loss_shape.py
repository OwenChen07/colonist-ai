"""How the new nets (round >= 150) lose to 2100+ players, against round 73's last
300 per account: length, ending, buildings at the end (from data/human/index.jsonl)."""

import json
import sys
from collections import Counter

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import load_rows, wilson  # noqa: E402

idx = {}
for line in open("data/human/index.jsonl"):
    if line.strip():
        r = json.loads(line)
        if r.get("status") == "ok":
            idx[r["capture"]] = r
cap = {}
for a in ("bot3", "bot4", "bot5", "bot7", "bot9"):
    for line in open(f"data/{a}/games.jsonl"):
        if line.strip():
            g = json.loads(line)
            cap[(a, g["at"])] = g
rows = load_rows()
for r in rows:
    g = cap.get((r["a"], r["at"])) or {}
    r["ix"] = idx.get(g.get("capture"))
    r["ended"] = g.get("ended")

new = [r for r in rows if (r["round"] or 0) >= 150]
old = []
for a in ("bot3", "bot4", "bot5", "bot7"):
    old += [r for r in rows if r["a"] == a and r["round"] == 73][-300:]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (float("nan"), 0)


def show(name, sel):
    print(f"--- {name}: n={len(sel)}")
    for won in (True, False):
        s = [r for r in sel if r["won"] == won]
        ix = [r["ix"] for r in s if r["ix"]]
        tag = "wins  " if won else "losses"
        print(
            f"  {tag} n={len(s)} indexed={len(ix)} endings={dict(Counter(r['ended'] for r in s))}"
        )
        if not ix:
            continue
        rolls = [x["rolls"] for x in ix]
        short = sum(1 for x in rolls if x < 60)
        lng = sum(1 for x in rolls if x >= 90)
        print(
            f"     rolls mean {mean(rolls)[0]:.0f}; <60: {short}/{len(ix)}  90+: {lng}/{len(ix)}"
        )
        for side in ("bot", "human"):
            parts = []
            for k in ("settlements", "cities", "roads", "dead_roads"):
                m, n = mean([x.get(side, {}).get(k) for x in ix])
                parts.append(f"{k} {m:.1f}")
            print(f"     {side:5s} " + ", ".join(parts))


def road_stat(name, sel, key):
    xs = [r["ix"]["bot"].get(key) for r in sel if r["ix"] and r["ix"].get("bot")]
    xs = [x for x in xs if x is not None]
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    se = sd / len(xs) ** 0.5
    print(
        f"  {name:22s} bot {key:12s} mean {m:.2f} +- {1.96 * se:.2f} (sd {sd:.2f}, n {len(xs)})"
    )


print("===== all opponents, all results")
for key in ("roads", "early_roads", "dead_roads", "settlements", "cities"):
    road_stat("new r150+", new, key)
    road_stat("r73 last 300/acct", old, key)
    for won in (True, False):
        tag = "win" if won else "loss"
        road_stat(f"  new {tag}", [r for r in new if r["won"] == won], key)
        road_stat(f"  r73 {tag}", [r for r in old if r["won"] == won], key)

for lo, hi in ((2100, 9999), (0, 2100)):
    print(f"===== opponents {lo}-{hi}")
    for name, sel in (("new r150+", new), ("r73 last 300/acct", old)):
        b = [r for r in sel if r["opp_mmr"] is not None and lo <= r["opp_mmr"] < hi]
        k = sum(r["won"] for r in b)
        print(f"{name}: {k}-{len(b)-k} {wilson(k, len(b))}")
        show(name, b)
