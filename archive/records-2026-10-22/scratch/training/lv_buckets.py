"""Late-value calibration by predicted-value bucket on the full late_value_set."""

import json
import sys
from multiprocessing import Pool

from catanatron.colonist import late_value_set as lvs

nets = sys.argv[1:]
rows = [json.loads(x) for x in open(lvs.SET)]
with Pool(4, lvs._init, (nets,)) as pool:
    res = [r for r in pool.imap_unordered(lvs._work, [r["capture"] for r in rows]) if r]
edges = [-1.01, -0.5, 0.0, 0.5, 0.8, 1.01]
for i, net in enumerate(nets):
    print(net)
    pairs = [
        (v[i], 1.0 if r["won"] else -1.0, r["capture"])
        for r in res
        for v in r["values"]
    ]
    n_all = len(pairs)
    for lo, hi in zip(edges, edges[1:]):
        b = [(v, z, c) for v, z, c in pairs if lo <= v < hi]
        if not b:
            continue
        n = len(b)
        mv = sum(v for v, _, _ in b) / n
        win = sum(z > 0 for _, z, _ in b) / n
        games = len({c for _, _, c in b})
        share = sum(v - z for v, z, _ in b) / n_all
        print(
            f"  v in [{lo:+.2f},{hi:+.2f}): n={n} games={games} implied {50 * (mv + 1):.1f}%"
            f" actual {100 * win:.1f}% | contributes {share:+.3f} of the total bias"
        )
