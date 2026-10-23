"""E54 step 30 live on Tedi4960 (bot9) from Oct 20, 8:41 PM, against bot9's round 152
baseline (ranked games before the switch) and Finlay3621 (bot10, round 73 control), by
band and against colonist's expected score. Harm stop (experiments.md): under 45% over
bot9's first 60 E54 games.

    .venv/bin/python data/orchestrator/scratch/live-loss/e54_live.py
"""

import math

from lvlib import compare, fmt_t, load_rows, summary

rows = load_rows()
bot9 = [r for r in rows if r["a"] == "bot9"]
# step 30 only: bot9 played it to games.jsonl line 164 (69-43); from Oct 21, 7:45 PM it plays
# E83's step 50 ('E54 iter50'), compared below with bot11's step 30 games from the same moment
arm = [r for r in bot9 if r["label"].startswith("E54 iter30")]
arm50 = [r for r in bot9 if r["label"].startswith("E54 iter50")]
# bot11 (fymouushii) is E54's second account from 9:33 PM; bot12's E54 games count as neither
arm11 = [r for r in rows if r["a"] == "bot11" and r["label"].startswith("E54 iter30")]
start50 = arm50[0]["at"] if arm50 else float("inf")
ctrl50 = [r for r in arm11 if r["at"] >= start50]
print(f"bot11 E54 ranked games {len(arm11)} (pooled into the arm)")
arm = sorted(arm + arm11, key=lambda r: r["at"])
start = arm[0]["at"] if arm else float("inf")
base = [r for r in bot9 if r["round"] == 152 and r["at"] < start]
# bot10 is round 73 until E89 (Oct 22, about 3:30 PM) moves it to E85 step 1 ('E85 step1, Oct 22')
ctrl = [r for r in rows if r["a"] == "bot10" and not r["label"].startswith("E85")]
e89 = [r for r in rows if r["a"] == "bot10" and r["label"].startswith("E85")]
ctrl_arm = [r for r in ctrl if r["at"] >= start]
# bot12 (wifedat) follows round 152 from about 10 PM Oct 20; its E54 games before that count as neither
b12 = [r for r in rows if r["a"] == "bot12" and not r["label"].startswith("E54")]
# bot8 (Vasos5306) is a best-net account from Oct 21, 10:16 AM (lvlib drops its people-model games)
b8 = [r for r in rows if r["a"] == "bot8"]


def expected(sel):
    rated = [r for r in sel if r["opp_mmr"] is not None and r["bot_mmr"] is not None]
    if not rated:
        return "no ratings"
    e = sum(1 / (1 + 10 ** ((r["opp_mmr"] - r["bot_mmr"]) / 400)) for r in rated) / len(
        rated
    )
    w = sum(r["won"] for r in rated) / len(rated)
    om = sum(r["opp_mmr"] for r in rated) / len(rated)
    return f"actual {w:.1%} vs expected {e:.1%} ({100 * (w - e):+.1f} pts, {len(rated)} rated, opp mean {om:.0f})"


print(
    f"E54 arm games {len(arm)}"
    + (f", {fmt_t(arm[0]['at'])} to {fmt_t(arm[-1]['at'])}" if arm else "")
)
print(f"bot10 control games {len(ctrl)} ({len(ctrl_arm)} since the E54 start)")
for name, sel in (
    ("E54 bot9", arm),
    ("r152 bot9 base", base),
    ("r73 bot10", ctrl),
    ("bot12 non-E54", b12),
    ("bot8 best net", b8),
):
    if sel:
        summary(name, sel)
        print(f"                {expected(sel)}")
compare("E54 - r152 base", arm, base)
compare("E54 - bot10", arm, ctrl)
if b12:
    print(f"bot12 non-E54 games {len(b12)}, labels {sorted({r['label'] for r in b12})}")
    compare("E54 - bot12 non-E54", arm, b12)
print(f"bot8 best-net games {len(b8)} (from Oct 21, 10:16 AM)")
if b8:
    compare("E54 - bot8 best net", arm, b8)


def quits(name, sel):
    """Opponent-quit share and finished-only win rate ('ended' is win / resignation;
    games logged before the field existed count as neither)."""
    k = [r for r in sel if r["ended"]]
    fin = [r for r in k if r["ended"] != "resignation"]
    if not k or not fin:
        print(f"  {name:16s} no 'ended' data")
        return None
    q = sum(r["ended"] == "resignation" for r in k) / len(k)
    fw = sum(r["won"] for r in fin)
    p = fw / len(fin)
    se = math.sqrt(p * (1 - p) / len(fin))
    print(
        f"  {name:16s} quit {q:5.1%} of {len(k):3d} | finished {fw}-{len(fin) - fw} {p:5.1%} ± {196 * se:.1f}"
    )
    return p, se


print("finished games only (person quits removed):")
fo = {
    name: quits(name, sel)
    for name, sel in (
        ("E54 arm", arm),
        ("r152 bot9 base", base),
        ("r73 bot10", ctrl),
        ("bot12 non-E54", b12),
        ("bot8 best net", b8),
    )
}
for other in ("r152 bot9 base", "r73 bot10", "bot8 best net"):
    if fo["E54 arm"] and fo[other]:
        (p, s), (pb, sb) = fo["E54 arm"], fo[other]
        d, h = 100 * (p - pb), 196 * math.sqrt(s * s + sb * sb)
        print(f"  finished-only E54 - {other}: {d:+.1f} [{d - h:+.1f}, {d + h:+.1f}]")
fin = lambda sel: [r for r in sel if r["ended"] and r["ended"] != "resignation"]
compare("finished-only E54 - r152 base", fin(arm), fin(base))
compare("finished-only E54 - bot10", fin(arm), fin(ctrl))
if arm:
    first = arm[:60]
    n, w = len(first), sum(r["won"] for r in first)
    p = w / n
    pb = sum(r["won"] for r in base) / len(base)
    se = math.sqrt(p * (1 - p) / n + pb * (1 - pb) / len(base))
    print(
        f"harm check (first 60): {w}-{n - w}, {p:.1%}; vs base {100 * (p - pb):+.1f} "
        f"[{100 * (p - pb - 1.96 * se):+.1f}, {100 * (p - pb + 1.96 * se):+.1f}] -> "
        f"{'HARM STOP' if n >= 60 and p < 0.45 else 'ok' if n >= 60 else 'before 60 games'}"
    )

print(
    f"\nE83 step 50 (bot9) vs step 30 (bot11) from {fmt_t(start50) if arm50 else 'not started'}:"
)
print(
    f"step 30 bot9 arm ends at {len([r for r in bot9 if r['label'].startswith('E54 iter30')])} ranked games "
    f"({sum(r['won'] for r in bot9 if r['label'].startswith('E54 iter30'))} won)"
)
for name, sel in (("step50 bot9", arm50), ("step30 bot11", ctrl50)):
    if sel:
        summary(name, sel)
        print(f"                {expected(sel)}")
compare("step 50 - step 30 (same window)", arm50, ctrl50)
quits("step50 bot9", arm50)
quits("step30 bot11", ctrl50)

start89 = e89[0]["at"] if e89 else float("inf")
print(
    f"\nE89 E85 step 1 (bot10) from {fmt_t(start89) if e89 else 'not started'}, "
    f"vs step 50 (bot9) same window and bot10's round 73 before:"
)
p50 = [r for r in arm50 if r["at"] >= start89]
for name, sel in (("E85s1 bot10", e89), ("step50 bot9", p50), ("r73 bot10", ctrl)):
    if sel:
        summary(name, sel)
        print(f"                {expected(sel)}")
if e89:
    compare("E85 step 1 - step 50 (same window)", e89, p50)
    compare("E85 step 1 - r73 bot10", e89, ctrl)
    quits("E85s1 bot10", e89)
