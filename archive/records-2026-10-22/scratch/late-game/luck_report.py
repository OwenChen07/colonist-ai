"""Dice luck vs board for 2300+ games (peak_story.json's rolls). For each side, over a
window of bot turns: cards actually paid by rolls, cards the board was expected to pay
(pips of its buildings, robber where it stood), and cards the robber cost (expected
without the robber minus with it). Luck = paid - expected. Aggregates only.
Usage: luck_report.py"""

import json
import random

G = json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
H = [
    g
    for g in G
    if g["mmr"] and g["mmr"] >= 2300 and g.get("replay") and g["replay"].get("rolls")
]


def boot(xs, reps=2000):
    if not xs:
        return "n=0"
    rng = random.Random(1)
    m = sum(xs) / len(xs)
    bs = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(reps))
    return f"{m:+.2f} [{bs[50]:+.2f}, {bs[-51]:+.2f}]"


def window(g, start):
    s = {f"{a}_{w}": 0.0 for a in ("got", "exp", "free") for w in ("bot", "opp")}
    n = 0
    for r in g["replay"]["rolls"]:
        if r["k"] < start or "got_bot" not in r:
            continue
        n += 1
        for w in ("bot", "opp"):
            s[f"got_{w}"] += r[f"got_{w}"]
            s[f"exp_{w}"] += r[f"exp_{w}"]
            s[f"free_{w}"] += r[f"free_{w}"]
    s["n"] = n
    return s


def table(label, S, start_of):
    W = [window(g, start_of(g)) for g in S]
    W = [w for w in W if w["n"]]
    print(
        f"\n-- {label}: {len(W)} games, rolls in window: median {sorted(w['n'] for w in W)[len(W) // 2] if W else '-'}"
    )
    for who in ("bot", "opp"):
        print(
            f"   {who}: paid {boot([w[f'got_{who}'] for w in W])}, expected {boot([w[f'exp_{who}'] for w in W])}, "
            f"luck {boot([w[f'got_{who}'] - w[f'exp_{who}'] for w in W])}, robber cost {boot([w[f'free_{who}'] - w[f'exp_{who}'] for w in W])}"
        )
    print(
        f"   opp - bot: expected {boot([w['exp_opp'] - w['exp_bot'] for w in W])}, luck {boot([(w['got_opp'] - w['exp_opp']) - (w['got_bot'] - w['exp_bot']) for w in W])}"
    )
    per = [((w["exp_opp"] - w["exp_bot"]) / w["n"]) for w in W]
    print(f"   board edge per roll (opp - bot expected): {boot(per)}")
    for who in ("bot", "opp"):
        a, b = [], []
        for g in S:
            rs = [r for r in g["replay"]["rolls"] if r["k"] >= start_of(g)]
            if rs:
                a.append(rs[0][f"free_{who}"])
                b.append(rs[-1][f"free_{who}"])
        print(
            f"   {who} board, cards per roll without robber: at window start {boot(a)} -> at end {boot(b)}"
        )


L = [g for g in H if not g["won"]]
Wn = [g for g in H if g["won"]]
print(f"2300+ games with rolls: {len(H)} (lost {len(L)}, won {len(Wn)})")
table(
    "LOSSES, peak 60-85, from the peak",
    [g for g in L if 60 <= g["peak"] <= 85],
    lambda g: g["peak_k"],
)
table(
    "LOSSES, peak 60-85, whole game",
    [g for g in L if 60 <= g["peak"] <= 85],
    lambda g: 0,
)
X = [g for g in H if g["first"]["70"] is not None and g["first"]["70"] <= 15]
table(
    "LOSSES after first reaching 70 (by bot turn 15), from the crossing",
    [g for g in X if not g["won"]],
    lambda g: g["first"]["70"],
)
table(
    "WINS after first reaching 70 (by bot turn 15), from the crossing",
    [g for g in X if g["won"]],
    lambda g: g["first"]["70"],
)
table("ALL 2300+ LOSSES, whole game", L, lambda g: 0)
table("ALL 2300+ WINS, whole game", Wn, lambda g: 0)
