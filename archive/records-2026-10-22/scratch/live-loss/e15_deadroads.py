"""E15 check: bot dead roads and roads-before-3rd-settlement per game, before vs
after each bot's E15 restart (capture-name times), from data/human/index.jsonl."""

import json
import math

CUT = {
    "bot3": "20261017-110600",
    "bot4": "20261017-053601",
    "bot5": "20261017-064000",
    "bot7": "20261017-105500",
}
SINCE = "20261015-000000"  # the before window: Oct 15 onward


def stamp(capture):
    return capture.removeprefix("bridge-").removesuffix(".json")


def mean_ci(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0
    return m, 1.96 * sd / math.sqrt(n), n


groups = {}
with open("data/human/index.jsonl") as stream:
    for line in stream:
        row = json.loads(line)
        acct = row.get("account")
        if acct not in CUT or row.get("status") != "ok" or "bot" not in row:
            continue
        t = stamp(row["capture"])
        if t < SINCE:
            continue
        side = "after" if t >= CUT[acct] else "before"
        for key in (acct, "all"):
            g = groups.setdefault((key, side), {"dead": [], "early": [], "won": []})
            g["dead"].append(row["bot"]["dead_roads"])
            g["early"].append(row["bot"]["early_roads"])
            g["won"].append(1 if row.get("bot_won") else 0)

for key in ("bot3", "bot4", "bot5", "bot7", "all"):
    parts = []
    for side in ("before", "after"):
        g = groups.get((key, side))
        if not g:
            parts.append(f"{side}: none")
            continue
        d, dc, n = mean_ci(g["dead"])
        e, ec, _ = mean_ci(g["early"])
        w = sum(g["won"]) / n
        parts.append(
            f"{side}: n={n} dead {d:.2f}±{dc:.2f} early {e:.2f}±{ec:.2f} win {w:.0%}"
        )
    print(key, " | ".join(parts))
for key in ("bot3", "bot4", "bot5", "bot7", "all"):
    b, a = groups.get((key, "before")), groups.get((key, "after"))
    if b and a:
        mb, cb, _ = mean_ci(b["dead"])
        ma, ca, _ = mean_ci(a["dead"])
        print(key, f"dead after-before {ma - mb:+.2f} ± {math.hypot(ca, cb):.2f}")
