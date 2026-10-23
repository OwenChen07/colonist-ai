"""late_value_set bias/MSE under the corrected labels vs the old (resignation = loss) labels.

Run from the worktree root: lv_compare.py <net.npz>
"""

import json
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "catanatron"))
from catanatron.colonist import late_value_set as L  # noqa: E402

ended = {}
for f in Path("data").glob("bot*/games.jsonl"):
    for line in open(f):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        # bot8 (Vasos5306) ran the people model until Oct 21, 10:16:43 AM Toronto, the best net after
        if f.parent.name == "bot8" and r.get("at", 0) < 1792592203:
            continue
        if "capture" in r:
            ended[f"captures/{r.get('account', f.parent.name)}/{r['capture']}"] = r.get(
                "ended"
            )

if __name__ == "__main__":
    rows = [json.loads(x) for x in open(L.SET)]
    with Pool(6, L._init, ([sys.argv[1]],)) as pool:
        res = [
            r for r in pool.imap_unordered(L._work, [r["capture"] for r in rows]) if r
        ]
    for name, lab in [
        ("fixed", lambda r: r["won"]),
        ("old", lambda r: r["won"] and ended.get(r["capture"]) != "resignation"),
    ]:
        pairs = [(v[0], 1.0 if lab(r) else -1.0) for r in res for v in r["values"]]
        first = [(r["values"][0][0], 1.0 if lab(r) else -1.0) for r in res]
        print(f"{name}: wins {sum(lab(r) for r in res)}/{len(res)}")
        print(f"  all {L._stats(pairs)} | v>0.5 {L._confident(pairs)}")
        print(f"  first {L._stats(first)} | v>0.5 {L._confident(first)}")
    # resignation games only
    res_g = [r for r in res if ended.get(r["capture"]) == "resignation"]
    pairs = [(v[0], 1.0 if r["won"] else -1.0) for r in res_g for v in r["values"]]
    print(f"resignation games {len(res_g)}: {L._stats(pairs)}")
    nr = [r for r in res if ended.get(r["capture"]) != "resignation"]
    pairs = [(v[0], 1.0 if r["won"] else -1.0) for r in nr for v in r["values"]]
    print(
        f"played-out games {len(nr)}: {L._stats(pairs)} | v>0.5 {L._confident(pairs)}"
    )
