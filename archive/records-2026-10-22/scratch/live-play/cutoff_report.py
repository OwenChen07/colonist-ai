"""E72 summary of cutoff_rows.jsonl (see cutoff_study.py for the definitions).

.venv/bin/python data/orchestrator/scratch/live-play/cutoff_report.py
"""

import json
import math
from collections import defaultdict

ROWS = "data/orchestrator/scratch/live-play/cutoff_rows.jsonl"
RATINGS = "data/orchestrator/analysis/opponent_ratings.json"


def wilson(k, n, z=1.96):
    if n == 0:
        return "   n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:5.1f}% [{100 * (c - h):4.1f}-{100 * (c + h):4.1f}] ({k}/{n})"


rows = [json.loads(l) for l in open(ROWS) if l.strip()]
bad = [r for r in rows if "error" in r or "bot" not in r or "opp" not in r]
rows = [r for r in rows if r not in bad]
ratings = json.load(open(RATINGS))
for r in rows:
    r["opp_mmr"] = (ratings.get(r["capture"]) or {}).get("opp_mmr")
print(f"games {len(rows)} (skipped {len(bad)})")


def subsets(rs):
    yield "all", rs
    yield "ranked", [r for r in rs if r["ranked"]]
    yield "ranked opp 2300+", [
        r for r in rs if r["ranked"] and (r["opp_mmr"] or 0) >= 2300
    ]
    yield "ranked opp <2100", [
        r for r in rs if r["ranked"] and r["opp_mmr"] and r["opp_mmr"] < 2100
    ]


def rates(rs, who, seat_key="seat"):
    lines = []
    for seat in (1, 2):
        s = [r for r in rs if r[who]["seat"] == seat]
        n = len(s)
        if not n:
            continue
        av = [r for r in s if r[who]["avail"]]
        lines.append(
            f"    {who} seat {seat}: wood+brick {wilson(sum(r[who]['wb'] for r in s), n)}"
            f" | cut {wilson(sum(r[who]['cut'] for r in s), n)}"
            f" | strict {wilson(sum(r[who]['strict'] for r in s), n)}"
        )
        lines.append(
            f"      cut+wb {wilson(sum(r[who]['cut'] and r[who]['wb'] for r in s), n)}"
            f" | spot available {wilson(len(av), n)}"
            f" | took it when available {wilson(sum(r[who]['cut'] and r[who]['wb'] for r in av), len(av))}"
        )
    return lines


print("\n== Seat split (bot's seat), bot win rate ==")
for name, rs in subsets(rows):
    print(
        f"  {name}: "
        + " | ".join(
            f"seat {s} {wilson(sum(r['won'] for r in rs if r['bot']['seat'] == s), sum(1 for r in rs if r['bot']['seat'] == s))}"
            for s in (1, 2)
        )
    )

print("\n== Wood+brick second settlement and the bot's win rate ==")
for name, rs in list(subsets(rows))[:2]:
    for who in ("bot", "opp"):
        for seat in (1, 2):
            s = [r for r in rs if r[who]["seat"] == seat]
            y = [r for r in s if r[who]["wb"]]
            n = [r for r in s if not r[who]["wb"]]
            print(
                f"  [{name}] {who} seat {seat} wood+brick: bot wins {wilson(sum(r['won'] for r in y), len(y))}"
                f" vs not {wilson(sum(r['won'] for r in n), len(n))}"
            )

print("\n== Q1: the opponent's second settlement ==")
for name, rs in subsets(rows):
    print(f"  [{name}]")
    for l in rates(rs, "opp"):
        print(l)
    for seat in (1, 2):  # opponent's seat
        s = [r for r in rs if r["opp"]["seat"] == seat]
        yes = [r for r in s if r["opp"]["cut"] and r["opp"]["wb"]]
        no = [r for r in s if not (r["opp"]["cut"] and r["opp"]["wb"])]
        cy = [r for r in s if r["opp"]["cut"]]
        cn = [r for r in s if not r["opp"]["cut"]]
        print(
            f"    bot win, opp seat {seat}: opp cut+wb {wilson(sum(r['won'] for r in yes), len(yes))}"
            f" vs not {wilson(sum(r['won'] for r in no), len(no))}"
            f" | any cut {wilson(sum(r['won'] for r in cy), len(cy))} vs not {wilson(sum(r['won'] for r in cn), len(cn))}"
        )

print("\n== Q2: our second settlement, by net ==")
by_net = defaultdict(list)
for r in rows:
    by_net[r["net"] or "other"].append(r)
for name, rs in [("all", rows)] + sorted(by_net.items()):
    print(f"  [net {name}] games {len(rs)}")
    for l in rates(rs, "bot"):
        print(l)
    for seat in (1, 2):
        s = [r for r in rs if r["bot"]["seat"] == seat]
        yes = [r for r in s if r["bot"]["cut"] and r["bot"]["wb"]]
        no = [r for r in s if not (r["bot"]["cut"] and r["bot"]["wb"])]
        print(
            f"    bot win, bot seat {seat}: we cut+wb {wilson(sum(r['won'] for r in yes), len(yes))}"
            f" vs not {wilson(sum(r['won'] for r in no), len(no))}"
        )
        av = [r for r in s if r["bot"]["avail"]]
        for key in ("play", "ref"):
            a = [r for r in av if f"{key}_mass" in r["bot"]]
            if not a:
                continue
            mass = sum(r["bot"][f"{key}_mass"] for r in a) / len(a)
            top = sum(r["bot"][f"{key}_best_rank"] == 1 for r in a)
            took_given_top = sum(
                r["bot"]["cut"] and r["bot"]["wb"]
                for r in a
                if r["bot"][f"{key}_best_rank"] == 1
            )
            took_not_top = sum(
                r["bot"]["cut"] and r["bot"]["wb"]
                for r in a
                if r["bot"][f"{key}_best_rank"] != 1
            )
            print(
                f"      seat {seat} spot available ({key} net prior, {len(a)} games): mean prior on cut+wb spots {mass:.2f};"
                f" a cut+wb spot is the prior's top {wilson(top, len(a))};"
                f" took it when top {wilson(took_given_top, top)}; when not top {wilson(took_not_top, len(a) - top)}"
            )
        a = [r for r in s if "play_chosen_rank" in r["bot"]]
        if a:
            print(
                f"      seat {seat}: search picked the prior's top spot {wilson(sum(r['bot']['play_chosen_rank'] == 1 for r in a), len(a))}"
            )
