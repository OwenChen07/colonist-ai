"""rob_target.json: the share-of-best gap with self-blocking placements removed,
and how often each side lands exactly on the best clean hex."""

import json
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from rob_target import OUT, boot, stat  # noqa: E402

res = json.load(open(OUT))
for oname, won in (("bot lost", False), ("bot won", True)):
    for lname, lo, hi in (("70+", 70, 9999), ("all", 0, 9999)):
        gs = [g for g in res if g["won"] == won and lo <= g["rolls"] < hi]
        print(f"== {oname}, {lname} rolls: {len(gs)} games")
        for who in ("bot", "person"):
            sel = lambda e, w=who: e["who"] == w and e["turn"] >= 20 and e["own"] == 0
            n = sum(1 for g in gs for e in g["rows"] if sel(e))
            print(f"  {who}, clean placements only: {n}")
            print(
                f"    share of best (pips)   {boot(gs, stat(sel, lambda e: e['vp'], lambda e: e['best_v']))}"
            )
            print(
                f"    share of best (o/w/s)  {boot(gs, stat(sel, lambda e: e['dv'], lambda e: e['best_dv']))}"
            )
            print(
                f"    on a best-pips hex     {boot(gs, stat(sel, lambda e: e['vp'] >= e['best_v']))}"
            )
            print(
                f"    victim 0 pips          {boot(gs, stat(sel, lambda e: e['vp'] == 0))}"
            )
            kn = lambda e, s=sel: s(e) and e["knight"]
            print(
                f"    knight moves, share    {boot(gs, stat(kn, lambda e: e['vp'], lambda e: e['best_v']))}"
            )
            sv = lambda e, s=sel: s(e) and not e["knight"]
            print(
                f"    seven moves, share     {boot(gs, stat(sv, lambda e: e['vp'], lambda e: e['best_v']))}"
            )
