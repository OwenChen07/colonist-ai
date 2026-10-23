"""How often did r73 (4 accounts, merged in time order) have a 30-game window at
12 wins or fewer? And bot8 (people model) over the same hours as r150+.

    .venv/bin/python data/orchestrator/scratch/live-loss/streak_check.py
"""

import json

from lvlib import fmt_t, load_rows

rows = load_rows()
old = [
    r for r in rows if r["round"] == 73 and r["a"] in ("bot3", "bot4", "bot5", "bot7")
]
new = [r for r in rows if (r["round"] or 0) >= 150]
w = [1 if r["won"] else 0 for r in old]
N, K = len(new), sum(1 for r in new if r["won"])
wins = [sum(w[i : i + N]) for i in range(len(w) - N + 1)]
bad = sum(1 for x in wins if x <= K)
print(
    f"r73: {len(w)} games, {len(wins)} windows of {N}; at <= {K} wins: {bad} ({bad / len(wins):.1%}); min {min(wins)}"
)
# per-account windows (each account's own sequence)
for a in ("bot3", "bot4", "bot5", "bot7"):
    s = [1 if r["won"] else 0 for r in old if r["a"] == a]
    ws = [sum(s[i : i + N]) for i in range(len(s) - N + 1)]
    print(
        f"  {a}: {len(ws)} windows, at <= {K}: {sum(1 for x in ws if x <= K)}, min {min(ws)}"
    )
t0, t1 = new[0]["at"], new[-1]["at"]
b8 = []
for line in open("data/bot8/games.jsonl"):
    if line.strip():
        g = json.loads(line)
        if (
            g.get("queue") == "ranked"
            and not g.get("own_bot")
            and g.get("winner") is not None
        ):
            b8.append(g)
same = [g for g in b8 if t0 <= g["at"] <= t1]
before = [g for g in b8 if g["at"] < t0][-300:]
f = lambda gs: sum(g["winner"] == g["bot_color"] for g in gs)
print(
    f"bot8 {fmt_t(t0)}..{fmt_t(t1)}: {f(same)}-{len(same) - f(same)}; last 300 before: {f(before)}-{len(before) - f(before)}"
)
print("bot8 rounds in window:", sorted({g.get("round") for g in same}, key=str))
