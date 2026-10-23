"""Leads lost despite non-negative dice luck vs leads kept with non-negative luck.

Joins lead_dice.json (luck after the lead turn) with led_late.json (state at lead
and at the end). Prints group means with bootstrap 95% CIs and rates with Wilson CIs.
"""

import json
import math
import random
import statistics as st
from pathlib import Path

HERE = Path(__file__).parent
dice = {d["path"]: d for d in json.load(open(HERE / "lead_dice.json"))}
games = {
    g["path"]: dict(g, won=g["won_fixed"])
    for g in json.load(open(HERE / "led_late_fixed.json"))
    if g["lead"]
}


def luck(d):
    return (d["act"][0] - d["exp"][0]) - (d["act"][1] - d["exp"][1])


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0, 0)
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, c - h, c + h


def boot(xs, n=4000):
    random.seed(0)
    if not xs:
        return (float("nan"),) * 3
    ms = sorted(st.mean(random.choices(xs, k=len(xs))) for _ in range(n))
    return st.mean(xs), ms[int(0.025 * n)], ms[int(0.975 * n)]


def feats(g, d):
    L, F = g["lead"], g["final"]
    me0, th0, me1, th1 = L["me"], L["them"], F["me"], F["them"]
    return {
        "v_lead": L["v"],
        "vp_margin_lead": me0["vp"] - th0["vp"],
        "turns_after": F["turn"] - L["turn"],
        "rolls_after": d["rolls"],
        "my_vp_gain": me1["vp"] - me0["vp"],
        "their_vp_gain": th1["vp"] - th0["vp"],
        "their_city_gain": th1["cities"] - th0["cities"],
        "their_sett_gain": (th1["setts"] + th1["cities"])
        - (th0["setts"] + th0["cities"]),
        "their_vpcard_gain": th1["vpcards"] - th0["vpcards"],
        "their_knight_gain": th1["knights"] - th0["knights"],
        "my_knight_gain": me1["knights"] - me0["knights"],
        "their_road_len_gain": th1["road_len"] - th0["road_len"],
        "luck": luck(d),
        "luck_me": d["act"][0] - d["exp"][0],
        "luck_them": d["act"][1] - d["exp"][1],
        "sevens_per_roll": d["sevens"] / max(1, d["rolls"]),
    }


def flags(g):
    L, F = g["lead"], g["final"]
    return {
        "road_stolen": L["me"]["road"] and not F["me"]["road"],
        "army_stolen": L["me"]["army"] and not F["me"]["army"],
        "they_gain_road": (not L["them"]["road"]) and F["them"]["road"],
        "they_gain_army": (not L["them"]["army"]) and F["them"]["army"],
        "had_road_at_lead": L["me"]["road"],
        "had_army_at_lead": L["me"]["army"],
        "they_had_army_at_lead": L["them"]["army"],
    }


groups = {"lost_luck>=0": [], "kept_luck>=0": [], "lost_luck<0": [], "kept_luck<0": []}
for p, g in games.items():
    d = dice.get(p)
    if not d:
        continue
    k = ("kept" if g["won"] else "lost") + ("_luck>=0" if luck(d) >= 0 else "_luck<0")
    groups[k].append((g, d))

print({k: len(v) for k, v in groups.items()})
order = ["lost_luck>=0", "kept_luck>=0", "lost_luck<0", "kept_luck<0"]
fkeys = list(feats(*groups[order[0]][0]).keys())
print("\nmeans [95% bootstrap]")
for f in fkeys:
    row = []
    for k in order:
        m, lo, hi = boot([feats(g, d)[f] for g, d in groups[k]])
        row.append(f"{m:6.2f} [{lo:5.2f},{hi:5.2f}]")
    print(f"{f:20s} " + " | ".join(row))
print("\npooled (all luck)")
for res in ("lost", "kept"):
    gs = groups[res + "_luck>=0"] + groups[res + "_luck<0"]
    for f in ("luck", "luck_me", "luck_them", "rolls_after"):
        m, lo, hi = boot([feats(g, d)[f] for g, d in gs])
        print(f"  {res} n={len(gs)} {f}: {m:+.2f} [{lo:+.2f},{hi:+.2f}]")
    xs = [flags(g)["road_stolen"] for g, d in gs]
    print(f"  {res} road_stolen {sum(xs)}/{len(xs)} {wilson(sum(xs), len(xs))}")
for k in ("luck<0", "luck>=0"):
    n_k, n_l = len(groups["kept_" + k]), len(groups["lost_" + k])
    print(f"  win rate with {k}: {n_k}/{n_k + n_l} {wilson(n_k, n_k + n_l)}")
lost = groups["lost_luck>=0"] + groups["lost_luck<0"]
kept = groups["kept_luck>=0"] + groups["kept_luck<0"]
random.seed(1)
diffs = sorted(
    st.mean(luck(d) for g, d in random.choices(lost, k=len(lost)))
    - st.mean(luck(d) for g, d in random.choices(kept, k=len(kept)))
    for _ in range(4000)
)
print(f"  luck lost-kept: [{diffs[100]:+.2f},{diffs[3900]:+.2f}]")
print("\nrates [Wilson 95%]")
for f in flags(groups[order[0]][0][0]):
    row = []
    for k in order:
        xs = [flags(g)[f] for g, d in groups[k]]
        p, lo, hi = wilson(sum(xs), len(xs))
        row.append(f"{sum(xs):2d}/{len(xs):2d} {p:4.0%} [{lo:3.0%},{hi:3.0%}]")
    print(f"{f:22s} " + " | ".join(row))

print("\nlost_luck>=0 games:")
for g, d in sorted(groups["lost_luck>=0"], key=lambda x: x[0]["lead"]["v"]):
    f, fl = feats(g, d), flags(g)
    print(
        f"{g['path'][9:]:40s} v={f['v_lead']:.2f} m={f['vp_margin_lead']:+d} "
        f"T+{f['turns_after']:2d} luck={f['luck']:+5.1f} myVP+{f['my_vp_gain']} "
        f"theirVP+{f['their_vp_gain']} cityΔ{f['their_city_gain']} settΔ{f['their_sett_gain']} "
        f"vpc+{f['their_vpcard_gain']} kn+{f['their_knight_gain']} "
        f"{'ROAD' if fl['road_stolen'] else ''} {'ARMY' if fl['army_stolen'] or fl['they_gain_army'] else ''}"
    )
