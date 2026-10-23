"""Stated win chance right after setup (bot's 2nd road) by band, and how often a
loss's 60%+ peak is only the opening. Reads peak_story.json."""

import json

G = json.load(open("data/orchestrator/scratch/late-game/peak_story.json"))
for name, lo, hi in (
    ("<2100", 0, 2100),
    ("2100-2300", 2100, 2300),
    ("2300+", 2300, 9999),
):
    S = [g for g in G if g["mmr"] and lo <= g["mmr"] < hi and g["open_p"] is not None]
    st = sum(g["open_p"] for g in S) / len(S)
    won = sum(g["won"] for g in S) / len(S)
    L = [g for g in S if not g["won"]]
    early = sum(g["peak"] >= 60 and g["peak_k"] <= 3 for g in L)
    late = sum(g["peak"] >= 60 and g["peak_k"] > 3 for g in L)
    late10 = sum(g["peak"] >= 60 and g["peak_k"] > 10 for g in L)
    print(
        f"{name}: n {len(S)}; stated after setup {st:.1f}, won {100 * won:.1f}; losses {len(L)}: 60%+ peak in bot turns 1-3 {early}, after turn 3 {late}, after turn 10 {late10}"
    )
    for a, b in ((0, 50), (50, 60), (60, 70), (70, 101)):
        X = [g for g in S if a <= g["open_p"] < b]
        if X:
            print(
                f"   setup stated {a}-{b}: n {len(X)}, mean {sum(g['open_p'] for g in X) / len(X):.0f}, won {100 * sum(g['won'] for g in X) / len(X):.0f}"
            )
