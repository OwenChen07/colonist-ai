"""E70: how the winner wins. The E54 exploiter's wins over round 152, round
152's wins over itself, and people's wins over the bot at 2100+ (live-loss's
loss_shape.py definitions: pieces on the board at the end, rolls).

    .venv/bin/python data/orchestrator/scratch/research/e70_stats.py
"""

import json
import math
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import load_rows  # noqa: E402

E70 = "data/orchestrator/e70"
KEYS = (
    "cities",
    "settlements",
    "roads",
    "rolls",
    "vp",
    "dev",
    "knights",
    "road_length",
)


def ci(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return "n/a"
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return f"{m:.2f} ± {1.96 * sd / math.sqrt(len(xs)):.2f}"


def share(flags):
    flags = [f for f in flags if f is not None]
    if not flags:
        return "n/a"
    p = sum(flags) / len(flags)
    return f"{p:.0%} ± {1.96 * math.sqrt(p * (1 - p) / len(flags)):.0%}"


def sim_side(final):
    dev = final["dev_played"]
    return {
        "cities": final["cities"],
        "settlements": final["settlements"],
        "roads": final["roads"],
        "rolls": final["rolls"],
        "vp": final["vp"],
        "dev": sum(dev.values()),
        "knights": dev["KNIGHT"],
        "road_length": final["road_length"],
        "lr": int(final["longest_road"]),
        "la": int(final["largest_army"]),
    }


def sim(name, winner_is=None):
    """(winner, loser) sides of each decided game; winner_is 'A' or 'B' filters."""
    out = []
    for line in open(f"{E70}/{name}.jsonl"):
        g = json.loads(line)
        if g["score"] == 0.5:
            continue
        a_won = g["score"] == 1.0
        if winner_is == "A" and not a_won or winner_is == "B" and a_won:
            continue
        w, l = ("RED", "BLUE") if a_won else ("BLUE", "RED")
        d = g["diagnostics"]
        out.append((sim_side(d[w]["final"]), sim_side(d[l]["final"])))
    return out


def people():
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
    groups = {"all nets": [], "round 73": [], "round 150+": []}
    for r in load_rows():
        if r["won"] or r["opp_mmr"] is None or r["opp_mmr"] < 2100:
            continue
        g = cap.get((r["a"], r["at"])) or {}
        x = idx.get(g.get("capture"))
        if not x or not x.get("human"):
            continue
        side = lambda s: {
            "cities": x[s].get("cities"),
            "settlements": x[s].get("settlements"),
            "roads": x[s].get("roads"),
            "rolls": x["rolls"],
        }
        pair = (side("human"), side("bot"))
        groups["all nets"].append(pair)
        if r["round"] == 73:
            groups["round 73"].append(pair)
        elif (r["round"] or 0) >= 150:
            groups["round 150+"].append(pair)
    return groups


def show(name, pairs):
    print(f"--- {name}: {len(pairs)} wins")
    for who, k in (("winner", 0), ("loser ", 1)):
        sides = [p[k] for p in pairs]
        parts = [f"{key} {ci([s.get(key) for s in sides])}" for key in KEYS]
        parts = [p for p in parts if not p.endswith("n/a")]
        if "lr" in sides[0]:
            parts.append(f"longest road {share([s['lr'] for s in sides])}")
            parts.append(f"largest army {share([s['la'] for s in sides])}")
        print(f"  {who} " + "; ".join(parts))
    short = sum(p[0]["rolls"] < 60 for p in pairs)
    long_ = sum(p[0]["rolls"] >= 90 for p in pairs)
    print(f"  rolls under 60: {short}, 90+: {long_} of {len(pairs)}")


show("E54 step 30's wins over round 152", sim("e54i30-vs-r152", "A"))
show("round 152's wins over E54 step 30", sim("e54i30-vs-r152", "B"))
show("round 152's wins over round 152 (control)", sim("r152-vs-r152"))
for name, pairs in people().items():
    show(f"people's wins over the bot at 2100+, {name}", pairs)
