"""Report for phantom.py's stats: target games listed, then target vs 2300+ band per metric
(mean, difference with a Welch 95% interval)."""

import json
import math
import sys
from collections import Counter

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import fmt_t  # noqa: E402
from phantom import OUT, SCAN, TARGETS  # noqa: E402

stats = json.loads(OUT.read_text())
scan = json.loads(SCAN.read_text())
games = {}
for line_file in (
    "ranked",
    "bot2",
    "bot3",
    "bot4",
    "bot5",
    "bot6",
    "bot7",
    "bot8",
    "bot9",
    "bot10",
    "bot11",
    "bot12",
):
    for line in open(f"data/{line_file}/games.jsonl"):
        if line.strip():
            g = json.loads(line)
            games[f"{line_file}/{g['capture']}"] = g

tgt = [(k, stats[k]) for k in TARGETS if stats.get(k)]
band = [(k, s) for k, s in stats.items() if s and not s["target"]]
print(f"target {len(tgt)} of {len(TARGETS)}, band {len(band)} (opp 2300+, replayed ok)")

print("\n1. The games")
for k, s in tgt:
    g = games.get(k, {})
    sc = scan.get(k) or {}
    won = g.get("winner") == g.get("bot_color")
    end = g.get("ended")
    fb, fp = s["final"]["bot"], s["final"]["person"]
    print(
        f"  {fmt_t(g.get('at', 0))} {k.split('/')[0]} {g.get('label')} | first: {s['first_mover']} | "
        f"bot {sc.get('bot_mmr')} opp {sc.get('opp_mmr')} | {'WIN' if won else 'loss'} by {end} | "
        f"{s['num_turns']} turns | VP bot {fb['vp']} opp {fp['vp']} | game {s['game_id']}"
    )


def welch(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / max(1, len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / max(1, len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return ma, mb, ma - mb, 1.96 * se


def row(label, f, side="person", sel=None):
    a = [f(s[side] if side else s) for _, s in tgt if sel is None or sel(s)]
    b = [f(s[side] if side else s) for _, s in band if sel is None or sel(s)]
    a = [x for x in a if x is not None]
    b = [x for x in b if x is not None]
    if len(a) < 1 or len(b) < 2:
        print(f"  {label:<44} n={len(a)}/{len(b)}")
        return
    ma, mb, d, h = (
        welch(a, b)
        if len(a) > 1
        else (a[0], sum(b) / len(b), a[0] - sum(b) / len(b), float("nan"))
    )
    print(
        f"  {label:<44} them {ma:6.2f} (n={len(a)})  band {mb:6.2f} (n={len(b)})  diff {d:+6.2f} ±{h:.2f}"
    )


def side_view(s, who):
    o = s["opening"][who]
    d = s["sides"][who]
    f = s["final"][who]
    early = [b for t, b in d["builds"] if t <= 10]
    first_city = next((t for t, b in d["builds"] if b == "C"), None)
    rob = d["rob"]
    clean = [r for r in rob if r["best"] > 0 and r["own"] == 0]
    return {
        "pips": o["pips"],
        "ow": o["ow"],
        "wb": o["wb"],
        "kinds": o["kinds"],
        "ports": len(o["ports"]),
        "e_R": early.count("R"),
        "e_S": early.count("S"),
        "e_C": early.count("C"),
        "e_D": early.count("D"),
        "first_city": first_city,
        "buys": d["buys"],
        "buy_rate": d["buys"] / max(1, d["turns"]),
        "knights": d["plays"].get("PLAY_KNIGHT_CARD", 0),
        "yop": d["plays"].get("PLAY_YEAR_OF_PLENTY", 0),
        "mono": d["plays"].get("PLAY_MONOPOLY", 0),
        "rb": d["plays"].get("PLAY_ROAD_BUILDING", 0),
        "robs": len(rob),
        "rob_share": (
            (sum(r["blocked"] for r in clean) / sum(r["best"] for r in clean))
            if clean
            else None
        ),
        "rob_best": (
            (sum(r["blocked"] >= r["best"] for r in clean) / len(clean))
            if clean
            else None
        ),
        "rob_self": (sum(r["own"] > 0 for r in rob) / len(rob)) if rob else None,
        "bank": d["bank"],
        "bank_rate": d["bank"] / max(1, d["turns"]),
        "lr_turn": d["first"].get("HAS_ROAD"),
        "la_turn": d["first"].get("HAS_ARMY"),
        "lr_ever": int("HAS_ROAD" in d["first"]),
        "la_ever": int("HAS_ARMY" in d["first"]),
        "lr_end": int(f["road"]),
        "la_end": int(f["army"]),
        "vp_cards": f["vp_cards"],
        "cities": f["cities"],
        "settlements": f["settlements"],
        "roads": f["roads"],
        "vp": f["vp"],
        "turns": d["turns"],
    }


for k, s in tgt + band:
    s["person"] = side_view(s, "person")
    s["bot"] = side_view(s, "bot")
    s["g"] = {
        "won": int(games[k]["winner"] == games[k]["bot_color"]),
        "first": int(s["first_mover"] == "person"),
        "turns": s["num_turns"],
    }

print(
    "\n  bot record: target",
    sum(s["g"]["won"] for _, s in tgt),
    "-",
    sum(1 - s["g"]["won"] for _, s in tgt),
    "| band",
    sum(s["g"]["won"] for _, s in band),
    "-",
    sum(1 - s["g"]["won"] for _, s in band),
)
print("  opp mmr (target):", [(scan.get(k) or {}).get("opp_mmr") for k, _ in tgt])
bm = [(scan.get(k) or {}).get("opp_mmr") for k, _ in band]
bm = [x for x in bm if x]
print(f"  opp mmr band mean {sum(bm) / len(bm):.0f}")

LABELS = [
    ("opening pips", "pips"),
    ("opening ore+wheat pips", "ow"),
    ("opening wood+brick pips", "wb"),
    ("opening resource kinds", "kinds"),
    ("opening ports", "ports"),
    ("turns 1-10: roads", "e_R"),
    ("turns 1-10: settlements", "e_S"),
    ("turns 1-10: cities", "e_C"),
    ("turns 1-10: dev cards", "e_D"),
    ("first city (own turn)", "first_city"),
    ("dev cards bought", "buys"),
    ("dev cards bought per turn", "buy_rate"),
    ("knights played", "knights"),
    ("year of plenty", "yop"),
    ("monopoly", "mono"),
    ("road building", "rb"),
    ("victory-point cards at end", "vp_cards"),
    ("robber moves", "robs"),
    ("robber: share of best block", "rob_share"),
    ("robber: on best hex", "rob_best"),
    ("robber: blocks itself", "rob_self"),
    ("bank/port trades per turn", "bank_rate"),
    ("longest road: first held (own turn)", "lr_turn"),
    ("largest army: first held (own turn)", "la_turn"),
    ("ever held longest road", "lr_ever"),
    ("ever held largest army", "la_ever"),
    ("holds longest road at end", "lr_end"),
    ("holds largest army at end", "la_end"),
    ("cities at end", "cities"),
    ("settlements at end", "settlements"),
    ("roads at end", "roads"),
    ("own turns", "turns"),
]
for who in ("person", "bot"):
    print(f"\n2. {who} side: target games vs 2300+ band")
    for label, key in LABELS:
        row(label, lambda v, k=key: v[k], side=who)
print("\n  game")
row("bot won", lambda g: g["won"], side="g")
row("person placed first", lambda g: g["first"], side="g")
row("game turns", lambda g: g["turns"], side="g")

print(
    "\n  per game (person | bot): won, pips, ow, wb, buys, knights, LR end, LA end, vp cards, cities, first city, rob_best"
)
for k, s in tgt:
    p, b = s["person"], s["bot"]
    f = lambda v: (
        v["pips"],
        v["ow"],
        v["wb"],
        v["buys"],
        v["knights"],
        v["lr_end"],
        v["la_end"],
        v["vp_cards"],
        v["cities"],
        v["first_city"],
        None if v["rob_best"] is None else round(v["rob_best"], 2),
    )
    print(f"  {k}: won {s['g']['won']} | {f(p)} | {f(b)}")
print(
    "\n  ports taken in openings (target):",
    Counter(p for _, s in tgt for p in s["opening"]["person"]["ports"]),
)
print("  opening numbers (target):", [s["opening"]["person"]["nums"] for _, s in tgt])
print(
    "  first-10 build strings (target):",
    ["".join(b for t, b in s["sides"]["person"]["builds"] if t <= 10) for _, s in tgt],
)
print(
    "  first-10 build strings (bot, target games):",
    ["".join(b for t, b in s["sides"]["bot"]["builds"] if t <= 10) for _, s in tgt],
)
