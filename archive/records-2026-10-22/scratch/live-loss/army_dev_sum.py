"""Knight-race summary from live-loss/army_dev_positions.json (aggregates only)."""

import json
from collections import Counter

rows = json.load(open("data/orchestrator/live-loss/army_dev_positions.json"))
for won in (False, True):
    sel = [r for r in rows if r["bot_won"] == won]
    gap0 = Counter(
        r["trigger"]["opp_knights"] - r["trigger"]["my_knights"] for r in sel
    )
    gap1 = Counter(r["final"]["opp_knights"] - r["final"]["my_knights"] for r in sel)
    held = Counter(r["trigger"]["my_hand"]["KNIGHT"] for r in sel)
    reach = sum(
        r["trigger"]["my_knights"] + r["trigger"]["my_hand"]["KNIGHT"]
        > r["trigger"]["opp_knights"]
        for r in sel
    )
    vpgap = Counter(r["final"]["my_vp"] - r["final"]["opp_vp"] for r in sel)
    print(f"bot {'won' if won else 'lost'} ({len(sel)} games)")
    print(f"  knight gap opp-me at trigger {sorted(gap0.items())}")
    print(f"  knight gap opp-me at end     {sorted(gap1.items())}")
    print(f"  bot knights in hand at trigger {sorted(held.items())}")
    print(f"  played+held knights > opp played at trigger: {reach}/{len(sel)}")
    print(f"  final vp me-opp {sorted(vpgap.items())}")
    # Bot's knight-legal decisions where it held back while behind in knights by <= held.
    late = [j for r in sel for j in r["judged"] if j["kind"] == "knight"]
    print(f"  knight-legal decisions {len(late)}, took {sum(j['took'] for j in late)}")
