"""Summaries over live-loss/late_bias_rows.json (late_bias.py's output)."""

import json
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from late_bias import ci  # noqa: E402

games = json.load(open("data/orchestrator/live-loss/late_bias_rows.json"))
bias = lambda r: r["v"] - r["z"]
print(f"games {len(games)}, with rows {sum(1 for g in games if g)}")
print(f"all: {ci(games, bias)}")
conf = [[r for r in g if r["v"] >= 0.5] for g in games]
print(f"v>=0.5: {ci(conf, bias)}  won {ci(conf, lambda r: (r['z'] + 1) / 2)}")
cells = {
    "dev0 army0": lambda r: r["opp_dev"] == 0 and not r["opp_army"],
    "dev1+ army0": lambda r: r["opp_dev"] >= 1 and not r["opp_army"],
    "dev0 army1": lambda r: r["opp_dev"] == 0 and r["opp_army"],
    "dev1+ army1": lambda r: r["opp_dev"] >= 1 and r["opp_army"],
    "dev2+ (any)": lambda r: r["opp_dev"] >= 2,
    "dev2+ and person has neither road nor army": lambda r: r["opp_dev"] >= 2
    and not r["opp_army"]
    and not r["opp_road"],
}
for name, f in cells.items():
    sub = [[r for r in g if r["v"] >= 0.5 and f(r)] for g in games]
    print(
        f"v>=0.5 {name}: bias {ci(sub, bias)}  won {ci(sub, lambda r: (r['z'] + 1) / 2)}"
    )
