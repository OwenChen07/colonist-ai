"""Price the robber stall (139 fix): bot win rate in ranked games against people,
split by whether the bot stalled (>STALL_S) after its own robber move with
nobody to rob. Control: games with such a robber move but no stall.
Only captures before CUTOFF (UTC name stamp). Ranked bot3/4/5/7, no own_bot.

    stall_price.py CUTOFF_UTC [STALL_S]
"""

import json
import sys
from pathlib import Path

from catanatron.colonist.loss_report import wilson

sys.path.insert(0, str(Path(__file__).parent))
from robber_stall import waits  # noqa: E402

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
CUTOFF = sys.argv[1]
STALL = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0


def main():
    idx = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok":
                idx[r["capture"]] = r
    groups = {"stall": [], "nostall_move": [], "no_move": []}
    turns_lost = []
    for a in ACCOUNTS:
        for line in open(f"data/{a}/games.jsonl"):
            if not line.strip():
                continue
            g = json.loads(line)
            if g.get("queue") != "ranked" or g.get("own_bot") or not g.get("capture"):
                continue
            stamp = g["capture"][len("bridge-") : -len(".json")]
            if stamp >= CUTOFF:
                continue
            path = Path("captures") / a / g["capture"]
            if not path.exists():
                continue
            try:
                w = waits(path)
            except Exception:
                continue
            if w is None:
                continue
            ix = idx.get(g["capture"])
            won = (
                ix["bot_won"]
                if ix and "bot_won" in ix
                else g.get("winner") == g.get("bot_color")
            )
            s = sum(x > STALL for x in w)
            key = "stall" if s else ("nostall_move" if w else "no_move")
            groups[key].append(bool(won))
            turns_lost.append(s)
    for k, v in groups.items():
        print(f"{k}: {wilson(sum(v), len(v))}")
    n = len(turns_lost)
    print(
        f"games {n}, stalled turns {sum(turns_lost)}, per game {sum(turns_lost) / max(n, 1):.3f}"
    )


if __name__ == "__main__":
    main()
