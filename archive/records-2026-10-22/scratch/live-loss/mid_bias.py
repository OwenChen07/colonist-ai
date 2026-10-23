"""late_bias.py over a turn window (default 20-60): where does the middle-game
value gap sit? Args: LAST T0 T1. Rows to live-loss/mid_bias_rows.json."""

import json
import os
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
import late_bias as L  # noqa: E402

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
L.T0 = int(sys.argv[2]) if len(sys.argv) > 2 else 20
T1 = int(sys.argv[3]) if len(sys.argv) > 3 else 60
NET = sys.argv[4] if len(sys.argv) > 4 else "runs/g1/best.npz"
TAG = sys.argv[5] if len(sys.argv) > 5 else ""
L.BOT = L.Net.load(NET)


class Obs(L.Obs):
    def step(self, game, decision):
        if game.state.num_turns >= T1:
            return
        super().step(game, decision)


L.Obs = Obs


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok" and "bot_won" in r}
    caps = sorted(rows)[-LAST:]
    games, skipped = [], 0
    for c in caps:
        r = rows[c]
        try:
            o = L.facts(os.path.join("captures", r["account"], c))
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            o = None
        if o is None:
            skipped += 1
            continue
        z = 1.0 if r["bot_won"] else -1.0
        for x in o.rows:
            x["z"] = z
        games.append(o.rows)
    print(f"games {len(games)}, skipped {skipped}, window {L.T0}-{T1}, net {NET}")
    bias = lambda r: r["v"] - r["z"]
    sq = lambda r: (r["v"] - r["z"]) ** 2
    print(f"all: bias {L.ci(games, bias)}  mse {L.ci(games, sq)}")
    splits = dict(L.SPLITS)
    splits["turn"] = lambda r: (r["turn"] // 10) * 10
    for conf in (False, True):
        tag = " | v>=0.5" if conf else ""
        for name, key in splits.items():
            print(f"\nby {name}{tag}:")
            for k in sorted({key(r) for g in games for r in g}):
                sub = [
                    [r for r in g if key(r) == k and (not conf or r["v"] >= 0.5)]
                    for g in games
                ]
                if sum(map(len, sub)) < 30:
                    continue
                print(
                    f"  {k}: bias {L.ci(sub, bias)}  mse {L.ci(sub, sq)[:6]}"
                    f"  won {L.ci(sub, lambda r: (r['z'] + 1) / 2)}"
                )
    with open(f"data/orchestrator/live-loss/mid_bias_rows{TAG}.json", "w") as f:
        json.dump(games, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
