"""E72 follow-up: seat split by opponent rating band, all captured ranked games.

Seat and result per game from live-play's open_rows.jsonl (E72 part 2, 5,215
games); both players' MMR at game time from the capture's game-over message
(cached with opp_rating.py's cache). Also fits win ~ Elo expectation + seat,
and asks whether the 2300+ seat gap is beyond the overall gap.

    .venv/bin/python data/orchestrator/scratch/live-loss/seat_band.py
"""

import json
import math
import random
import sys
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.loss_report import wilson

CACHE = Path("data/orchestrator/analysis/opponent_ratings.json")
BANDS = ((0, 2100), (2100, 2300), (2300, 9999))


def mmrs(path):
    you = out = None
    for f in load_capture(path):
        d = f.data.get("data") if isinstance(f.data, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p = d["payload"]
        if you is None and "playerColor" in p:
            you = p["playerColor"]
        if "rankedUserStates" in p:
            out = p["rankedUserStates"]
    if you is None or not out:
        return None
    me = [u["rankedState"] for u in out if u.get("color") == you]
    them = [u["rankedState"] for u in out if u.get("color") != you]
    if len(me) != 1 or len(them) != 1:
        return None
    return {
        "bot_mmr": me[0].get("previousMMR"),
        "opp_mmr": them[0].get("previousMMR"),
        "bot_change": me[0].get("mostRecentChangeInValue"),
    }


def find(cap, acct):
    for a in [acct] + [p.name for p in Path("captures").iterdir()]:
        p = Path("captures") / a / cap
        if p.exists():
            return p
    return None


def diff_ci(w1, n1, w2, n2):
    p1, p2 = w1 / n1, w2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    d = p1 - p2
    return 100 * d, 100 * 1.96 * se, se


def main():
    acct = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            acct[r["capture"]] = r.get("account")
    rows = [
        json.loads(l)
        for l in open("data/orchestrator/scratch/live-play/open_rows.jsonl")
        if l.strip()
    ]
    rows = [r for r in rows if r.get("ranked")]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    new = 0
    for r in rows:
        cap = r["capture"]
        if cap in cache:
            continue
        p = find(cap, acct.get(cap) or "ranked")
        try:
            cache[cap] = mmrs(p) if p else None
        except Exception as error:  # noqa: BLE001
            print(f"{cap}: {error!r}", file=sys.stderr)
            cache[cap] = None
        new += 1
        if new % 300 == 0:
            CACHE.write_text(json.dumps(cache, indent=0))
    CACHE.write_text(json.dumps(cache, indent=0))

    data = []  # (bot seat 1?, won, opp_mmr, bot_mmr)
    for r in rows:
        m = cache.get(r["capture"])
        if m and m.get("opp_mmr") is not None and m.get("bot_mmr") is not None:
            data.append(
                (r["bot"]["seat"] == 1, bool(r["won"]), m["opp_mmr"], m["bot_mmr"])
            )
    print(
        f"{len(rows)} ranked rows, {len(data)} with both ratings ({new} captures read now)"
    )

    def split(label, sel):
        a = [d for d in sel if d[0]]
        b = [d for d in sel if not d[0]]
        wa, wb = sum(d[1] for d in a), sum(d[1] for d in b)
        g, h, _ = diff_ci(wa, len(a), wb, len(b)) if a and b else (0, 0, 0)
        exp_a = sum(1 / (1 + 10 ** ((d[2] - d[3]) / 400)) for d in a) / max(len(a), 1)
        exp_b = sum(1 / (1 + 10 ** ((d[2] - d[3]) / 400)) for d in b) / max(len(b), 1)
        # person's view: person seat 1 = bot seat 2
        print(
            f"{label:>10}: bot seat 1 {wilson(wa, len(a))} (exp {100*exp_a:.1f}) | bot seat 2 "
            f"{wilson(wb, len(b))} (exp {100*exp_b:.1f}) | gap {g:+.1f} ± {h:.1f}; person seat 1 "
            f"{wilson(len(b) - wb, len(b))} vs seat 2 {wilson(len(a) - wa, len(a))}"
        )
        return g, h

    print("seat split by opponent rating at game time")
    gaps = {}
    for lo, hi in BANDS:
        gaps[(lo, hi)] = split(
            f"{lo}-{hi}" if hi < 9999 else f"{lo}+",
            [d for d in data if lo <= d[2] < hi],
        )
    allg = split("all", data)

    # 2300+ vs the rest: is the band's gap beyond the overall gap?
    top = [d for d in data if d[2] >= 2300]
    rest = [d for d in data if d[2] < 2300]
    gt, ht, st = diff_ci(
        sum(d[1] for d in top if d[0]),
        sum(d[0] for d in top),
        sum(d[1] for d in top if not d[0]),
        sum(not d[0] for d in top),
    )
    gr, hr, sr = diff_ci(
        sum(d[1] for d in rest if d[0]),
        sum(d[0] for d in rest),
        sum(d[1] for d in rest if not d[0]),
        sum(not d[0] for d in rest),
    )
    dd = gt - gr
    print(
        f"2300+ gap {gt:+.1f} ± {ht:.1f} vs under-2300 gap {gr:+.1f} ± {hr:.1f}: "
        f"difference {dd:+.1f} ± {1.96*100*math.hypot(st, sr):.1f} (z {dd/(100*math.hypot(st, sr)):.2f})"
    )

    # logistic fit: logit P(win) = a*elo_logit + b + c*seat1
    X = [(math.log(10) * (d[3] - d[2]) / 400, 1.0, 1.0 if d[0] else 0.0) for d in data]
    y = [1.0 if d[1] else 0.0 for d in data]
    w = [0.0, 0.0, 0.0]
    for _ in range(50):
        g = [0.0] * 3
        H = [[0.0] * 3 for _ in range(3)]
        for x, t in zip(X, y):
            z = sum(a * b for a, b in zip(w, x))
            p = 1 / (1 + math.exp(-z))
            for i in range(3):
                g[i] += (t - p) * x[i]
                for j in range(3):
                    H[i][j] += p * (1 - p) * x[i] * x[j]
        # solve H d = g (3x3, Gaussian elimination)
        M = [H[i][:] + [g[i]] for i in range(3)]
        for i in range(3):
            piv = M[i][i]
            for j in range(i, 4):
                M[i][j] /= piv
            for k in range(3):
                if k != i:
                    f = M[k][i]
                    for j in range(i, 4):
                        M[k][j] -= f * M[i][j]
        w = [w[i] + M[i][3] for i in range(3)]
    # covariance = H^-1 (invert by solving with unit vectors)
    inv = []
    for e in range(3):
        M = [H[i][:] + [1.0 if i == e else 0.0] for i in range(3)]
        for i in range(3):
            piv = M[i][i]
            for j in range(i, 4):
                M[i][j] /= piv
            for k in range(3):
                if k != i:
                    f = M[k][i]
                    for j in range(i, 4):
                        M[k][j] -= f * M[i][j]
        inv.append([M[i][3] for i in range(3)])
    se = math.sqrt(inv[2][2])
    base = 1 / (1 + math.exp(-w[1]))
    print(
        f"logistic: rating slope {w[0]:.2f} (1 = Elo exact), intercept {w[1]:+.2f} (win {100*base:.1f}% at equal ratings, seat 2),"
        f" seat 1 {w[2]:+.3f} ± {1.96*se:.3f} logit = {100*(1/(1+math.exp(-w[1]-w[2])) - base):+.1f} points at equal ratings"
    )
    # randomisation check: share of bot seat 1 by band
    for lo, hi in BANDS:
        s = [d for d in data if lo <= d[2] < hi]
        print(
            f"  bot in seat 1, {lo}-{hi}: {wilson(sum(d[0] for d in s), len(s))}; opp mean "
            f"{sum(d[2] for d in s)/len(s):.0f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
