"""Summary of road_join_count.json: turns where a joining road (+2 or more to
the bot's longest road) was legal and no join was built that turn."""

import json
import math
from collections import Counter

rows = json.load(open("data/orchestrator/live-loss/road_join_count.json"))


def wilson(k, n, z=1.96):
    if n == 0:
        return "n/a"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{k}/{n} = {100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}]"


def stake(r):
    return r["best_join_lr"] >= 5 and (
        r["holder"] == "opp" or (r["opp_lr"] >= 4 and r["opp_lr"] >= r["lr_now"] - 1)
    )


def takes(r):
    # the join alone would take or keep Longest Road from the opponent
    return r["best_join_lr"] > r["opp_lr"] and r["holder"] != "bot"


won = {}
for acct in ("bot3", "bot4", "bot5", "bot7"):
    for line in open(f"data/{acct}/games.jsonl"):
        r = json.loads(line)
        if r.get("capture"):
            won[f"captures/{acct}/{r['capture']}"] = r.get("winner") == r.get(
                "bot_color"
            )
for g in rows:
    g["bot_won"] = won[g["capture"]]
ok = [g for g in rows if "rows" in g]
print("overall bot wins", wilson(sum(g["bot_won"] for g in ok), len(ok)))
print(
    "games",
    len(rows),
    "replayed",
    len(ok),
    "errors",
    Counter(g.get("error") for g in rows if "error" in g),
)
cats = {
    "any": lambda r: True,
    "stake": stake,
    "stake+takes": lambda r: stake(r) and takes(r),
}
for name, f in cats.items():
    flagged, wins, passed_turns = [], 0, 0
    road_else = Counter()
    later = 0
    for g in ok:
        turns = {}
        for r in g["rows"]:
            turns.setdefault(r["turn"], []).append(r)
        bad = []
        for t, rs in turns.items():
            if any(r["took"] for r in rs):
                continue
            if any(f(r) for r in rs if not r["took"]):
                bad.append((t, rs))
        if bad:
            flagged.append(g)
            wins += g["bot_won"]
            passed_turns += len(bad)
            for t, rs in bad:
                road_else[
                    (
                        "road elsewhere"
                        if any(r["played"][0] == "BUILD_ROAD" for r in rs)
                        else "no road"
                    )
                ] += 1
            first = min(t for t, _ in bad)
            later += any(r["took"] for r in g["rows"] if r["turn"] > first)
    others = [g for g in ok if g not in flagged]
    print(
        f"\n[{name}] games with a passed-up join: {wilson(len(flagged), len(ok))}; turns {passed_turns}"
    )
    print(
        f"  bot won those: {wilson(wins, len(flagged))}; other games: {wilson(sum(g['bot_won'] for g in others), len(others))}"
    )
    print(f"  turns: {dict(road_else)}; joined on a later turn: {later}/{len(flagged)}")
    adv = Counter()
    for g in flagged:
        for r in g["rows"]:
            if not r["took"] and f(r):
                adv[r["best_join_lr"] - r["lr_now"]] += 1
    print("  join gain over current length (decisions):", dict(sorted(adv.items())))
