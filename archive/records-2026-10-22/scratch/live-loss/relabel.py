"""Relabel led_late.json outcomes from data/<bot>/games.jsonl (winner == bot_color).

The replay's winner is None when the person resigns, so led_late.py counted
resignation wins as losses. Writes led_late_fixed.json and prints the confusion.
"""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[3]
truth = {}
for f in ROOT.glob("data/bot*/games.jsonl"):
    for line in open(f):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        # bot8 (Vasos5306) ran the people model until Oct 21, 10:16:43 AM Toronto, the best net after
        if f.parent.name == "bot8" and r.get("at", 0) < 1792592203:
            continue
        if "capture" in r and r.get("winner") and r.get("bot_color"):
            truth[f"captures/{r.get('account', f.parent.name)}/{r['capture']}"] = (
                r["winner"] == r["bot_color"],
                r.get("ended"),
            )

games = json.load(open(HERE / "led_late.json"))
conf = Counter()
missing = 0
for g in games:
    t = truth.get(g["path"])
    if t is None:
        missing += 1
        g["won_fixed"], g["ended"] = g["won"], None
        continue
    g["won_fixed"], g["ended"] = t
    conf[(g["lead"] is not None, g["won"], t[0], t[1])] += 1
print(f"games {len(games)}, no games.jsonl row {missing}")
for k, v in sorted(conf.items(), key=str):
    print(f"  led={k[0]} replay_won={k[1]} true_won={k[2]} ended={k[3]}: {v}")
json.dump(games, open(HERE / "led_late_fixed.json", "w"))

lv = Counter()
for line in open(ROOT / "data/orchestrator/live-loss/late_value_set.jsonl"):
    r = json.loads(line)
    t = truth.get(r["capture"])
    lv[(r["won"], t)] += 1
print("late_value_set (label, truth):", dict(lv))
