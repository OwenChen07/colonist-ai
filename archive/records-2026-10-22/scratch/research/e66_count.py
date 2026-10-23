"""E66 step 1: opponent's pre-game rating for every people-game shard, and
how many games and labelled moves fall in each band (train and holdout).

    .venv/bin/python data/orchestrator/scratch/research/e66_count.py [SHARDS]

SHARDS is opponent_exact (default). The rating map (shard game id -> opp MMR)
is cached in data/orchestrator/e66/ratings.json for the training step.
"""

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np

from catanatron.colonist.capture import load as load_capture

ROOT = Path("/home/owen/colonist-ai")
KIND = sys.argv[1] if len(sys.argv) > 1 else "opponent_exact"
OUT = ROOT / "data/orchestrator/e66/ratings.json"
SHARED = ROOT / "data/orchestrator/analysis/opponent_ratings.json"
BANDS = ((0, 2100), (2100, 2300), (2300, 9999))


def opp_mmr(path):
    you = out = None
    for f in load_capture(path):
        d = f.data.get("data") if isinstance(f.data, dict) else None
        if not isinstance(d, dict) or not isinstance(d.get("payload"), dict):
            continue
        p = d["payload"]
        if you is None and "playerColor" in p:
            you = p["playerColor"]
        if "rankedUserStates" in p:
            out = p["rankedUserStates"]
    if you is None or not out:
        return None
    them = [u["rankedState"] for u in out if u.get("color") != you]
    return them[0].get("previousMMR") if len(them) == 1 else None


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(OUT.read_text()) if OUT.exists() else {}
    shared = json.loads(SHARED.read_text()) if SHARED.exists() else {}
    by_game = {}
    for line in open(ROOT / "data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok" and "game" in r:
                by_game[f"{int(r['game']):019d}"] = r
    rows = {}
    for split in ("train", "holdout"):
        for path in sorted(
            glob.glob(str(ROOT / f"data/human/{split}/{KIND}/shard_*.npz"))
        ):
            gid = os.path.basename(path).split("_")[1]
            if gid not in cache:
                r = by_game.get(gid)
                mmr = None
                if r:
                    s = shared.get(r["capture"])
                    if isinstance(s, dict):
                        mmr = s.get("opp_mmr")
                    else:
                        cap = ROOT / "captures" / r["account"] / r["capture"]
                        try:
                            mmr = opp_mmr(cap) if cap.exists() else None
                        except Exception as error:
                            print(f"{cap}: {error}", file=sys.stderr)
                cache[gid] = {"mmr": mmr, "account": r["account"] if r else None}
            with np.load(path) as shard:
                n = int((shard["action"] >= 0).sum())
            rows[path] = (split, cache[gid]["mmr"], n)
    OUT.write_text(json.dumps(cache))
    for split in ("train", "holdout"):
        print(f"{split}:")
        items = [(m, n) for s, m, n in rows.values() if s == split]
        none = [n for m, n in items if m is None]
        print(f"  no rating: {len(none)} games, {sum(none)} moves")
        for lo, hi in BANDS:
            b = [n for m, n in items if m is not None and lo <= m < hi]
            print(f"  {lo}-{hi}: {len(b)} games, {sum(b)} moves")
    rated = [m for _, m, _ in rows.values() if m is not None]
    print("opp MMR quantiles 10/50/90:", np.percentile(rated, [10, 50, 90]).round())


if __name__ == "__main__":
    main()
