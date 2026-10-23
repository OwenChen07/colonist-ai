"""Top-1 and cross-entropy of the main net and the people model on the target's moves vs the
2300+ band's moves (opponent_exact shards), split by train/holdout."""

import glob
import json
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from phantom import OUT, TARGETS  # noqa: E402

from catanatron.colonist.net import Net  # noqa: E402
from catanatron.colonist.opponent_model import score  # noqa: E402

stats = json.loads(OUT.read_text())
index = {}
for line in open("data/human/index.jsonl"):
    r = json.loads(line)
    if r.get("status") == "ok":
        index[f"{r['account']}/{r['capture']}"] = r


def shard(key):
    r = index.get(key)
    s = stats.get(key)
    if not r or not s:
        return None
    hits = glob.glob(
        f"data/human/{r['split']}/opponent_exact/shard_{r['game']:019d}_{s['them']}_opponent_exact.npz"
    )
    return (hits[0], r["split"]) if hits else None


tgt = [shard(k) for k in TARGETS]
band = [shard(k) for k, s in stats.items() if s and not s["target"]]
print(
    f"shards: target {sum(x is not None for x in tgt)}/{len(tgt)}, band {sum(x is not None for x in band)}/{len(band)}"
)
nets = {"main": Net.load("runs/g1/best.npz"), "human": Net.load("models/human/net.npz")}
groups = {
    "target all": [p for p, _ in filter(None, tgt)],
    "target holdout": [p for p, sp in filter(None, tgt) if sp == "holdout"],
    "band all": [p for p, _ in filter(None, band)],
    "band holdout": [p for p, sp in filter(None, band) if sp == "holdout"],
}
for name, paths in groups.items():
    if not paths:
        continue
    sc = score(paths, nets)
    line = "  ".join(
        f"{n} top-1 {v['accuracy']:.1%} ce {v['cross_entropy']:.3f}"
        for n, v in sc.items()
    )
    print(
        f"{name:>15} ({len(paths)} games, {sc['human']['rows']} moves): {line}",
        flush=True,
    )
for key, x in zip(TARGETS, tgt):
    if x:
        sc = score([x[0]], nets)
        print(
            f"  {key} {x[1]}: people top-1 {sc['human']['accuracy']:.1%} ce {sc['human']['cross_entropy']:.3f}"
            f" | main top-1 {sc['main']['accuracy']:.1%} ce {sc['main']['cross_entropy']:.3f} ({sc['human']['rows']} moves)"
        )
