"""Late-value bias split by dice luck after the lead (live-loss lead_dice.json)."""

import json
import sys
from collections import Counter
from multiprocessing import Pool

from catanatron.colonist import late_value_set as lvs

nets = sys.argv[1:]
dice = {
    d["path"]: d
    for d in json.load(open("data/orchestrator/scratch/live-loss/lead_dice.json"))
}
print("dice labels", Counter(d["dice"] for d in dice.values()))
rows = [json.loads(x) for x in open(lvs.SET)]
paths = [r["capture"] for r in rows if r["capture"] in dice]
print("overlap games", len(paths), "of", len(dice))
with Pool(4, lvs._init, (nets,)) as pool:
    res = [r for r in pool.imap_unordered(lvs._work, paths) if r]


def luck(d):
    return (d["act"][0] - d["exp"][0]) - (d["act"][1] - d["exp"][1])


for i, net in enumerate(nets):
    print(net)
    for name, keep in [
        ("all", lambda d: True),
        ("luck>=0", lambda d: luck(d) >= 0),
        ("luck<0", lambda d: luck(d) < 0),
        ("label balanced+", lambda d: d["dice"] != "bad"),
        ("label bad", lambda d: d["dice"] == "bad"),
    ]:
        sub = [r for r in res if keep(dice[r["capture"]])]
        pairs = [(v[i], 1.0 if r["won"] else -1.0) for r in sub for v in r["values"]]
        won = sum(r["won"] for r in sub)
        print(f"  {name:16s} games {len(sub)} won {won}: {lvs._stats(pairs)}")
        print(f"  {'':16s} v>0.5 {lvs._confident(pairs)}")
