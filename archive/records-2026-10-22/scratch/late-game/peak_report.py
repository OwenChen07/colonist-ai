"""Report for peak_story.json (see peak_story.py). Aggregates only.
Usage: peak_report.py [since_epoch]"""

import json
import math
import random
import sys
from collections import defaultdict
from statistics import median

G = json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
if len(sys.argv) > 1:
    G = [g for g in G if g["at"] >= int(sys.argv[1])]
BANDS = (("<2100", 0, 2100), ("2100-2300", 2100, 2300), ("2300+", 2300, 9999))


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.0f}, {100 * (c + h):.0f}] ({k}/{n})"


def boot_mean(xs, reps=2000):
    if not xs:
        return "n=0"
    rng = random.Random(1)
    m = sum(xs) / len(xs)
    bs = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(reps))
    return f"{m:.2f} [{bs[50]:.2f}, {bs[-51]:.2f}]"


def q(xs):
    xs = sorted(xs)
    if not xs:
        return "-"
    return f"median {xs[len(xs) // 2]}, IQR {xs[len(xs) // 4]}-{xs[3 * len(xs) // 4]}"


def band(g):
    m = g["mmr"]
    if m is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= m < hi:
            return name


print(f"games with curves {len(G)}, rated {sum(1 for g in G if g['mmr'])}")
for name, _, _ in BANDS:
    S = [g for g in G if band(g) == name]
    L = [g for g in S if not g["won"]]
    W = [g for g in S if g["won"]]
    print(
        f"\n== {name}: {wilson(len(W), len(S))} won; quits by opp/bot: {sum(g['ended']=='win' and False for g in S)}"
    )
    print(
        f"  losses with peak >= 60: {wilson(sum(g['peak'] >= 60 for g in L), len(L))}; >= 75: {wilson(sum(g['peak'] >= 75 for g in L), len(L))}"
    )
    print(
        f"  loss peak: {q([g['peak'] for g in L])}; peak bot turn: {q([g['peak_k'] for g in L])}; game length (bot turns) {q([g['last_k'] for g in L])}"
    )
    print(
        f"  win  peak: {q([g['peak'] for g in W])}; win length {q([g['last_k'] for g in W])}"
    )
    print(
        "  first crossing (once the bot's stated chance first reaches T after setup, how often it won):"
    )
    for T in ("60", "70", "80", "90"):
        X = [g for g in S if g["first"][T] is not None]
        k = sum(g["won"] for g in X)
        print(
            f"    T={T}: {len(X)} games crossed ({wilson(len(X), len(S))} of games), won {wilson(k, len(X))}; crossing turn {q([g['first'][T] for g in X])}"
        )
    print("  calibration at bot turn k (stated bins vs won):")
    for kk in ("10", "15", "20", "25"):
        row = []
        for lo, hi in ((0, 40), (40, 60), (60, 75), (75, 90), (90, 101)):
            X = [
                g
                for g in S
                if g["at_turn"][kk] is not None and lo <= g["at_turn"][kk] < hi
            ]
            if X:
                st = sum(g["at_turn"][kk] for g in X) / len(X)
                row.append(
                    f"{lo}-{hi}: said {st:.0f} won {100 * sum(g['won'] for g in X) / len(X):.0f} (n {len(X)})"
                )
        print(f"    k={kk}: " + "; ".join(row))

# replays, 2300+
H = [g for g in G if band(g) == "2300+" and g.get("replay")]
print(f"\n== 2300+ replays: {len(H)}")


FROM = None  # None: from the peak; "70": from the first crossing of 70


def after_peak(g):
    r = g["replay"]
    pk = g["peak_k"] if FROM is None else g["first"][FROM]
    c = defaultdict(lambda: defaultdict(int))
    for k, who, kind in r["ev"]:
        if k >= pk:
            c[who][kind] += 1
    snap = r["snap"].get(str(pk))
    return c, snap, r["final"], r["final"]["turn"], max(1, g["last_k"] - pk + 1)


KINDS = (
    "income",
    "dev_buy",
    "knight",
    "monopoly",
    "yop",
    "road_building",
    "city",
    "settlement",
    "road",
    "robber_on_other",
    "bank_trade",
    "discard",
)


def table(label, S):
    print(f"\n-- {label}: {len(S)} games")
    if not S:
        return
    turns = [after_peak(g)[4] for g in S]
    print(
        f"   bot turns from peak to end: {q(turns)}; peak {q([g['peak'] for g in S])}; peak turn {q([g['peak_k'] for g in S])}"
    )
    for who in ("bot", "opp"):
        parts = []
        for kind in KINDS:
            xs = [after_peak(g)[0][who][kind] for g in S]
            per10 = 10 * sum(xs) / sum(turns)
            parts.append(f"{kind} {sum(xs) / len(xs):.2f} ({per10:.2f}/10t)")
        print(f"   {who}: " + ", ".join(parts))
    for who in ("bot", "opp"):
        snaps = [after_peak(g) for g in S]
        snaps = [s for s in snaps if s[1]]
        vp0 = [s[1][who]["vp"] for s in snaps]
        vp1 = [s[2][who]["vp"] for s in snaps]
        print(
            f"   {who} VP at peak {boot_mean(vp0)} -> end {boot_mean(vp1)}; devs in hand at peak {boot_mean([s[1][who]['devhand'] for s in snaps])}; cities at peak {boot_mean([s[1][who]['cities'] for s in snaps])} -> end {boot_mean([s[2][who]['cities'] for s in snaps])}"
        )
        f = [s[2][who] for s in snaps]
        print(
            f"     at end: road {sum(x['road'] for x in f)}/{len(f)}, army {sum(x['army'] for x in f)}/{len(f)}, VP cards {boot_mean([x['vpcards'] for x in f])}, knights {boot_mean([x['knights'] for x in f])}, settlements {boot_mean([x['setts'] for x in f])}, cities {boot_mean([x['cities'] for x in f])}"
        )
        p = [s[1][who] for s in snaps]
        print(
            f"     at peak: road {sum(x['road'] for x in p)}/{len(p)}, army {sum(x['army'] for x in p)}/{len(p)}"
        )
    # who took road/army after the peak
    snaps = [after_peak(g) for g in S]
    snaps = [s for s in snaps if s[1]]
    for prize in ("road", "army"):
        gain = sum(1 for s in snaps if s[2]["opp"][prize] and not s[1]["opp"][prize])
        lost_b = sum(1 for s in snaps if s[1]["bot"][prize] and not s[2]["bot"][prize])
        print(
            f"   {prize}: opp gained it after the peak in {gain}/{len(snaps)}; bot lost it after the peak in {lost_b}/{len(snaps)}"
        )
    # VP margin at peak
    print(
        f"   VP lead at peak (bot - opp, actual): {boot_mean([s[1]['bot']['vp'] - s[1]['opp']['vp'] for s in snaps])}; public lead {boot_mean([s[1]['bot']['pub'] - s[1]['opp']['pub'] for s in snaps])}"
    )


table(
    "2300+ LOSSES, peak 60-85", [g for g in H if not g["won"] and 60 <= g["peak"] <= 85]
)
table("2300+ LOSSES, peak > 85", [g for g in H if not g["won"] and g["peak"] > 85])
FROM = "70"
X = [g for g in H if g["first"]["70"] is not None and g["first"]["70"] <= 15]
print("\n######## from the first crossing of 70 (by bot turn 15)")
table("2300+ LOSSES after reaching 70", [g for g in X if not g["won"]])
table("2300+ WINS after reaching 70", [g for g in X if g["won"]])
