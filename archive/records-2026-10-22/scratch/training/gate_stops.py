"""Replay past progress-gate matches from runs/train.log: the cumulative score
at 1000/2000/3000/4000 games, the final result, and what a second futility
look would have done. Each chunk (futility 1000, the rest 3000, extension 4000)
logs its own counts, so cumulative scores are rebuilt per chunk."""

import re
import sys

LOG = "/home/owen/colonist-ai/runs/train.log"
start = re.compile(r"round (\d+): promotion match \((\w+) gate")
prog = re.compile(r"^\[[\d:]+\]\s+(\d+) games: ([\d.]+)%")
final = re.compile(r"([\d.]+)% ± [\d.]+% over (\d+) games")

rounds = []
cur = None
for line in open(LOG):
    m = start.search(line)
    if m:
        cur = {"round": int(m[1]), "gate": m[2], "chunks": [], "promoted": False}
        rounds.append(cur)
        continue
    if cur is None:
        continue
    if "evaluation games by host" in line:
        cur["chunks"].append([])
        continue
    m = prog.match(line)
    if m and cur["chunks"]:
        cur["chunks"][-1].append((int(m[1]), float(m[2]) / 100))
        continue
    m = final.search(line)
    if m:
        cur["final"] = (float(m[1]) / 100, int(m[2]))
    if "promoted" in line and "round" in line and "final" in cur:
        cur["promoted"] = True
    if "kept round" in line:
        cur["kept"] = True

rows = []
for r in rounds:
    if r["gate"] != "progress" or "final" not in r:
        continue
    # cumulative (n, wins) points
    pts = []
    base_n, base_w = 0, 0.0
    for ch in r["chunks"]:
        ch = [p for p in ch]
        if not ch:
            continue
        for n, s in ch:
            pts.append((base_n + n, base_w + n * s))
        n, s = ch[-1]
        base_n, base_w = base_n + n, base_w + n * s
    at = {}
    for n, w in pts:
        if n in (1000, 2000, 3000, 4000, 6000, 8000):
            at[n] = w / n
    r["at"] = at
    rows.append(r)

min_round = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rows = [r for r in rows if r["round"] >= min_round]
print("round  @1000  @2000  @3000  @4000  final/games  outcome")
for r in rows:
    a = r["at"]
    f = lambda n: f"{a[n]:.3f}" if n in a else "  -  "
    out = "PROMOTED" if r["promoted"] and not r.get("kept") else "kept"
    print(
        f"{r['round']:5d}  {f(1000)}  {f(2000)}  {f(3000)}  {f(4000)}  "
        f"{r['final'][0]:.4f}/{r['final'][1]:<5d} {out}"
    )

games = sum(r["final"][1] for r in rows)
print(
    f"\n{len(rows)} rounds, {games} gate games, {games / max(len(rows), 1):.0f} a round"
)
for thr in (0.490, 0.495, 0.500, 0.505):
    saved = 0
    lost = []
    for r in rows:
        s2 = r["at"].get(2000)
        if s2 is None or r["final"][1] <= 2000:
            continue
        if s2 < thr:
            saved += r["final"][1] - 2000
            if r["promoted"] and not r.get("kept"):
                lost.append(r["round"])
    print(
        f"stop at 2000 if score < {thr:.1%}: saves {saved} games "
        f"({saved / max(games, 1):.0%}), loses promotions {lost}"
    )
for thr in (0.502, 0.504, 0.505, 0.506):
    saved, lost, n = 0, [], 0
    for r in rows:
        s4 = r["at"].get(4000)
        if r["final"][1] == 8000 and s4 is not None and s4 < thr:
            saved += 4000
            n += 1
            if r["promoted"] and not r.get("kept"):
                lost.append(r["round"])
    print(
        f"extend only from {thr:.1%}: {n} fewer extensions, saves {saved} games "
        f"({saved / max(games, 1):.0%}), loses promotions {lost}"
    )
for thr in (0.505, 0.51, 0.515):
    saved = 0
    lost = []
    for r in rows:
        s1 = r["at"].get(1000)
        if s1 is None or r["final"][1] <= 1000:
            continue
        # 95% margin at 1000 games, as the log shows (about 3.0%)
        if s1 + 0.030 < thr:
            saved += r["final"][1] - 1000
            if r["promoted"] and not r.get("kept"):
                lost.append(r["round"])
    print(
        f"futility-below {thr:.1%} at 1000: saves {saved} more games "
        f"({saved / max(games, 1):.0%}), loses promotions {lost}"
    )
