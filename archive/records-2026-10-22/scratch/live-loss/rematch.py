"""Rematch study (owner's question, Oct 22): after a game, how often is the next ranked
game against the same opponent, by previous result and gap; and how we do in those
rematches. Scans captures once for opponent hash, MMRs and frame times; cache in
data/orchestrator/analysis/rematch_scan.json. No names leave this script.

Usage: rematch.py [scan|report]"""

import hashlib
import json
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lvlib import FINE, band_rates, fmt_t, standardized, wilson  # noqa: E402

from catanatron.colonist.capture import load as load_capture  # noqa: E402

DIRS = (
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
)
CACHE = Path("data/orchestrator/analysis/rematch_scan.json")
E83_START = 1792626300  # Oct 21, 7:45 PM Toronto


def scan(path):
    you = None
    names = {}
    ranked = None
    t0 = t1 = tg = None
    for f in load_capture(path):
        t0 = f.t if t0 is None else t0
        t1 = f.t
        d = f.data.get("data") if isinstance(f.data, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p = d["payload"]
        if you is None and "playerColor" in p:
            you = p["playerColor"]
            tg = f.t
        if "rankedUserStates" in p:
            ranked = p["rankedUserStates"]
        gs = p.get("playerUserStates")
        if isinstance(gs, list):
            for u in gs:
                if isinstance(u, dict) and "selectedColor" in u and u.get("username"):
                    names[u["selectedColor"]] = u["username"]
    out = {"opp": None, "bot_mmr": None, "opp_mmr": None, "t0": t0, "tg": tg, "t1": t1}
    if you is None:
        return out
    them = [n for c, n in names.items() if c != you]
    if len(them) == 1:
        out["opp"] = hashlib.sha256(them[0].encode()).hexdigest()[:8]
    if ranked:
        me = [u["rankedState"] for u in ranked if u.get("color") == you]
        ot = [u["rankedState"] for u in ranked if u.get("color") != you]
        if len(me) == 1 and len(ot) == 1:
            out["bot_mmr"] = me[0].get("previousMMR")
            out["opp_mmr"] = ot[0].get("previousMMR")
    return out


def job(args):
    key, path = args
    try:
        return key, scan(path) if Path(path).exists() else None
    except Exception as error:
        print(f"{key}: {error}", file=sys.stderr)
        return key, None


def all_rows():
    rows = []
    for a in DIRS:
        p = Path(f"data/{a}/games.jsonl")
        if not p.exists():
            continue
        for i, line in enumerate(open(p)):
            if line.strip():
                g = json.loads(line)
                g["dir"] = a
                g["i"] = i
                rows.append(g)
    return rows


def do_scan():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = []
    for g in all_rows():
        if g.get("queue", "ranked") != "ranked" or g.get("own_bot"):
            continue
        key = f"{g['dir']}/{g['capture']}"
        if key not in cache:
            todo.append((key, f"captures/{key}"))
    print(f"scanning {len(todo)}", flush=True)
    with Pool(4) as pool:
        for n, (key, out) in enumerate(pool.imap_unordered(job, todo, chunksize=8)):
            cache[key] = out
            if n % 500 == 499:
                CACHE.write_text(json.dumps(cache))
                print(f"{n + 1} done", flush=True)
    CACHE.write_text(json.dumps(cache))
    print("SCAN DONE", flush=True)


def gap_bucket(s):
    if s is None:
        return "unknown"
    if s < 60:
        return "<1 min"
    if s < 180:
        return "1-3 min"
    return ">3 min"


def report():
    cache = json.loads(CACHE.read_text())
    by_dir = defaultdict(list)
    for g in all_rows():
        by_dir[g["dir"]].append(g)
    pairs = []  # (prev, next) both ranked human games, adjacent in the account's log
    allg = []
    for a, gs in by_dir.items():
        gs.sort(key=lambda g: (g["at"], g["i"]))
        for prev, nxt in zip(gs, gs[1:]):
            pass
        info = []
        for g in gs:
            human = (
                g.get("queue", "ranked") == "ranked"
                and not g.get("own_bot")
                and g.get("winner") is not None
            )
            s = cache.get(f"{a}/{g['capture']}") or {}
            info.append((g, human, s))
            if human:
                allg.append(
                    {
                        "a": a,
                        "at": g["at"],
                        "won": g["winner"] == g["bot_color"],
                        "opp_mmr": s.get("opp_mmr"),
                        "bot_mmr": s.get("bot_mmr"),
                        "opp": s.get("opp"),
                    }
                )
        for (g0, h0, s0), (g1, h1, s1) in zip(info, info[1:]):
            if not (h0 and h1 and s0.get("opp") and s1.get("opp")):
                continue
            end0 = s0.get("t1") / 1000 if s0.get("t1") else g0["at"]
            gap = (s1["tg"] / 1000 - end0) if s1.get("tg") else None
            pairs.append(
                {
                    "a": a,
                    "at": g1["at"],
                    "prev_won": g0["winner"] == g0["bot_color"],
                    "same": s0["opp"] == s1["opp"],
                    "gap": gap,
                    "won": g1["winner"] == g1["bot_color"],
                    "opp_mmr": s1.get("opp_mmr"),
                    "bot_mmr": s1.get("bot_mmr"),
                    "ended": g1.get("ended"),
                    "prev_ended": g0.get("ended"),
                }
            )
    print(
        f"{len(allg)} ranked human games, {len(pairs)} adjacent pairs with both opponents known"
    )
    print(
        f"from {fmt_t(min(r['at'] for r in allg))} to {fmt_t(max(r['at'] for r in allg))}"
    )
    gaps = sorted(p["gap"] for p in pairs if p["gap"] is not None)
    if gaps:
        q = lambda f: gaps[int(f * (len(gaps) - 1))]
        print(
            f"gap (prev end -> next game state) seconds: p10 {q(.1):.0f} p50 {q(.5):.0f} p90 {q(.9):.0f}; negative {sum(x < 0 for x in gaps)}"
        )

    print("\n1. Same opponent as the previous game")
    for label, sel in (
        ("after a win", [p for p in pairs if p["prev_won"]]),
        ("after a loss", [p for p in pairs if not p["prev_won"]]),
        ("all", pairs),
    ):
        print(f"  {label:>12}: {wilson(sum(p['same'] for p in sel), len(sel))}")
        for b in ("<1 min", "1-3 min", ">3 min", "unknown"):
            s = [p for p in sel if gap_bucket(p["gap"]) == b]
            if s:
                print(f"      {b:>8}: {wilson(sum(p['same'] for p in s), len(s))}")

    rl = [p for p in pairs if p["same"] and not p["prev_won"]]
    rw = [p for p in pairs if p["same"] and p["prev_won"]]
    nl = [p for p in pairs if not p["same"] and not p["prev_won"]]
    print("\n2. Our results in rematches (win rate of the second game)")
    for label, sel in (
        ("rematch after loss", rl),
        ("rematch after win", rw),
        ("new opp after loss", nl),
        ("all games", allg),
    ):
        mo = [r["opp_mmr"] for r in sel if r["opp_mmr"]]
        mb = [r["bot_mmr"] for r in sel if r["bot_mmr"]]
        print(
            f"  {label:>20}: {wilson(sum(r['won'] for r in sel), len(sel))}  opp {sum(mo) / max(1, len(mo)):.0f} bot {sum(mb) / max(1, len(mb)):.0f}"
        )

    def cmp(label, x, y):
        d, lo, hi = standardized(band_rates(x, FINE), band_rates(y, FINE))
        print(
            f"  {label}: same 100-point band {100 * d:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}]"
        )

    cmp("rematch after loss - all games", rl, allg)
    cmp("rematch after loss - rematch after win", rl, rw)
    cmp("rematch after win - all games", rw, allg)
    cmp("new opp after loss - all games", nl, allg)
    for b in ("<1 min", "1-3 min", ">3 min"):
        s = [p for p in rl if gap_bucket(p["gap"]) == b]
        if s:
            print(
                f"    rematch after loss, gap {b}: {wilson(sum(p['won'] for p in s), len(s))}"
            )
    # how the rematches end
    print(
        "  rematch-after-loss endings:",
        dict(sorted(defaultdict(int, {}).items())) or "",
        {e: sum(1 for p in rl if p["ended"] == e) for e in {p["ended"] for p in rl}},
    )
    print(
        "  previous-game endings (loss -> rematch):",
        {
            e: sum(1 for p in rl if p["prev_ended"] == e)
            for e in {p["prev_ended"] for p in rl}
        },
    )

    # per-account rematch rate after loss
    print("\n  per account, rematch rate after a loss:")
    for a in DIRS:
        s = [p for p in pairs if p["a"] == a and not p["prev_won"]]
        if s:
            print(f"    {a:>6}: {wilson(sum(p['same'] for p in s), len(s))}")

    # streaks: same opponent 3+ in a row
    print("\n4. Top band: Tedi4960 (bot9) since Oct 21, 7:45 PM")
    t = [p for p in pairs if p["a"] == "bot9" and p["at"] >= E83_START]
    print(
        f"  same opponent as previous game: {wilson(sum(p['same'] for p in t), len(t))}"
    )
    for label, s in (
        ("after a win", [p for p in t if p["prev_won"]]),
        ("after a loss", [p for p in t if not p["prev_won"]]),
    ):
        print(f"    {label}: {wilson(sum(p['same'] for p in s), len(s))}")
    g9 = [r for r in allg if r["a"] == "bot9" and r["at"] >= E83_START]
    opps = defaultdict(int)
    for r in g9:
        opps[r["opp"]] += 1
    print(
        f"  {len(g9)} games, {len(opps)} distinct opponents; top counts {sorted(opps.values(), reverse=True)[:8]}"
    )
    top = [p for p in pairs if (p["opp_mmr"] or 0) >= 2400]
    print(
        f"  all accounts, next opponent 2400+: rematch {wilson(sum(p['same'] for p in top), len(top))}"
    )
    hi = [p for p in pairs if (p["bot_mmr"] or 0) >= 2300]
    print(
        f"  all accounts, bot at 2300+: rematch {wilson(sum(p['same'] for p in hi), len(hi))}"
    )
    hl = [p for p in hi if not p["prev_won"]]
    print(f"    after a loss: {wilson(sum(p['same'] for p in hl), len(hl))}")


if __name__ == "__main__":
    if sys.argv[1:] == ["scan"]:
        do_scan()
    else:
        report()
