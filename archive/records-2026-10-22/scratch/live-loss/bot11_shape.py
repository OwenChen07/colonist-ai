"""bot11 (step 30) loss shape: its games before Oct 21, 7:45 PM Toronto (bot9 switched nets
on the same VM) against the games since. Turns, quits, move times, peak win chance, opponents.
"""

import json
import re
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import compare, fmt_t, load_rows, summary, wilson  # noqa: E402

CUT = 1792626300  # Oct 21, 7:45 PM Toronto
PAT = re.compile(r"win chance (\d+)%, (\d+) ms, (\d+) sims")


def curves(a):
    out = {}
    moves = []
    for line in open(f"data/{a}/bridge.log", errors="replace"):
        if "game started" in line:
            moves = []
        m = PAT.search(line)
        if m and "move:" in line:
            moves.append(
                (
                    int(m.group(1)),
                    int(m.group(2)),
                    int(m.group(3)),
                    line.split("move:")[1].split("(")[0].strip(),
                )
            )
        if "game saved to" in line and moves:
            out[line.rstrip().rsplit("/", 1)[-1]] = moves
            moves = []
    return out


caps = {}
for a in ("bot11", "bot9"):
    for line in open(f"data/{a}/games.jsonl"):
        if line.strip():
            g = json.loads(line)
            caps[(a, g["at"])] = g
rows = load_rows()
cv = {a: curves(a) for a in ("bot11", "bot9")}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def show(name, sel, a):
    print(
        f"\n===== {name}: n={len(sel)}  {fmt_t(sel[0]['at'])} .. {fmt_t(sel[-1]['at'])}"
        if sel
        else name
    )
    if not sel:
        return
    summary(name[:14], sel)
    print("  endings all:", dict(Counter(r["ended"] for r in sel)))
    for won in (True, False):
        s = [r for r in sel if r["won"] == won]
        print(
            f"  {'wins' if won else 'losses'} {len(s)}: endings {dict(Counter(r['ended'] for r in s))}"
        )
        rl = [r["rolls"] for r in s if r["rolls"]]
        if rl:
            print(
                f"     rolls median {st.median(rl):.0f} mean {st.mean(rl):.0f}; <60 {sum(x < 60 for x in rl)}/{len(rl)}; 90+ {sum(x >= 90 for x in rl)}/{len(rl)}"
            )
        om = [r["opp_mmr"] for r in s if r["opp_mmr"]]
        if om:
            print(
                f"     opp mmr mean {st.mean(om):.0f} median {st.median(om):.0f} (n {len(om)})"
            )
        bm = [r["bot_mmr"] for r in s if r["bot_mmr"]]
        if bm:
            print(f"     bot mmr mean {st.mean(bm):.0f}")
        c = [cv[a].get(caps[(a, r["at"])]["capture"]) for r in s]
        c = [x for x in c if x]
        if not c:
            continue
        pk = [max(m[0] for m in x) for x in c]
        nm = [len(x) for x in c]
        print(
            f"     curves {len(c)}: peak median {st.median(pk):.0f}; peak>=60 {wilson(sum(p >= 60 for p in pk), len(pk))}; peak>=75 {sum(p >= 75 for p in pk)}"
        )
        print(f"     moves/game median {st.median(nm):.0f}")
    allm = []
    for r in sel:
        x = cv[a].get(caps[(a, r["at"])]["capture"])
        if x:
            allm += x
    ms = [m[1] for m in allm]
    sims = [m[2] for m in allm]
    big = [m for m in allm if m[2] >= 2400]
    print(
        f"  moves {len(allm)}: ms median {st.median(ms):.0f} p90 {pct(ms, .9)} p99 {pct(ms, .99)} max {max(ms)}; >=10s {sum(x >= 10000 for x in ms)}; >=20s {sum(x >= 20000 for x in ms)}"
    )
    print(f"  sims: {dict(Counter(sims).most_common(5))}")
    if big:
        bms = [m[1] for m in big]
        print(
            f"  full-search moves {len(big)}: ms median {st.median(bms):.0f} p90 {pct(bms, .9)}; ms per 1000 sims {1000 * st.median([m[1] / m[2] for m in big]):.0f}"
        )


b11 = [r for r in rows if r["a"] == "bot11"]
pre = [r for r in b11 if r["at"] < CUT]
post = [r for r in b11 if r["at"] >= CUT]
print(
    "labels pre",
    Counter(r["label"] for r in pre),
    "post",
    Counter(r["label"] for r in post),
)
show("bot11 before", pre, "bot11")
show("bot11 since", post, "bot11")
print()
compare("bot11 since vs before", post, pre)
# halves of 'since' to see whether it is steady or a step
h = len(post) // 2
compare("bot11 since 1st half vs before", post[:h], pre)
compare("bot11 since 2nd half vs before", post[h:], pre)
b9 = [r for r in rows if r["a"] == "bot9"]
show("bot9 before", [r for r in b9 if r["at"] < CUT], "bot9")
show("bot9 since", [r for r in b9 if r["at"] >= CUT], "bot9")
# hour-of-day mix (Toronto)
for name, sel in (("before", pre), ("since", post)):
    hrs = Counter(
        int(fmt_t(r["at"])[7:9]) % 12 + (12 if fmt_t(r["at"]).endswith("PM") else 0)
        for r in sel
    )
    night = sum(v for k, v in hrs.items() if k < 8)
    print(f"bot11 {name}: games midnight-8 AM {night}/{len(sel)}")
