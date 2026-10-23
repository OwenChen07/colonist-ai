"""Bound the replay's VP-card timing guess. The end summary gives how many VP
cards the person kept, but not when they were bought; resolve_hidden_draws gives
VP to the person's OLDEST unplayed hidden draws. Here: the same games under that
rule and under NEWEST-first, (a) share of games where the person held a VP card
in turns 20-59, by result, (b) mean VP cards held per turn at turn 60+.
Usage: vp_timing.py [LAST]. No net."""

import json
import os
import random
import sys
from collections import Counter

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.record import BUY, END_TURN, PLAYED_CARD

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def leftover(record, color):
    """Turns of `color`'s hidden draws never matched to a play (same matching as
    resolve_hidden_draws), and the game's last turn."""
    turn, known, hidden = 0, Counter(), []
    for action, result in record["log"]:
        c, kind = action[0], action[1]
        if kind == END_TURN:
            turn += 1
        elif c != color:
            continue
        elif kind == BUY:
            if result is None:
                hidden.append(turn)
            else:
                known[(result, turn)] += 1
        elif kind in PLAYED_CARD:
            card = PLAYED_CARD[kind]
            held = sorted(
                t for (k, t), n in known.items() if k == card and t < turn and n
            )
            if held:
                known[(card, held[0])] -= 1
                continue
            opts = [h for h in hidden if h < turn]
            if opts:
                hidden.remove(opts[0])
    return hidden, turn


def held(vp_turns, t):
    return sum(1 for b in vp_turns if b <= t)


def boot(xs, reps=2000):
    rng = random.Random(0)
    m = sum(xs) / len(xs)
    v = sorted(
        sum(xs[rng.randrange(len(xs))] for _ in xs) / len(xs) for _ in range(reps)
    )
    return f"{m:.3f} [{v[int(.025 * reps)]:.3f}, {v[int(.975 * reps) - 1]:.3f}] (n {len(xs)})"


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok" and "bot_won" in r}
    out, skipped = [], 0
    for cap in sorted(rows)[-LAST:]:
        r = rows[cap]
        try:
            rec = convert(
                load_capture(os.path.join("captures", r["account"], cap))
            ).record
            you = [c for c, p in rec["players"].items() if p["you"]]
            them = next(c for c in rec["seats"] if c != you[0])
            hid, end = leftover(rec, them)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        k = min(rec.get("hidden_vp_cards", {}).get(them, 0), len(hid))
        g = {
            "won": bool(r["bot_won"]),
            "k": k,
            "n_left": len(hid),
            "end": end,
            "has_summary": "hidden_vp_cards" in rec,
        }
        for name, vp in (("old", hid[:k]), ("new", hid[len(hid) - k :] if k else [])):
            g[name + "_mid"] = any(held(vp, t) for t in range(20, 60))
            late = [held(vp, t) for t in range(60, end + 1)]
            g[name + "_late"] = late
        out.append(g)
    print(
        f"games {len(out)}, skipped {skipped}, with summary {sum(g['has_summary'] for g in out)}"
    )
    for won in (True, False):
        sub = [g for g in out if g["won"] == won]
        print(
            f"\nbot {'won' if won else 'lost'} ({len(sub)}): VP cards kept {boot([g['k'] for g in sub])}"
            f", unplayed hidden draws {boot([g['n_left'] for g in sub])}"
        )
        for name in ("old", "new"):
            print(
                f"  {name}-first: held VP in turns 20-59 {boot([float(g[name + '_mid']) for g in sub])}"
            )
    for name in ("old", "new"):
        late = [x for g in out for x in g[name + "_late"]]
        print(
            f"{name}-first: mean VP held per turn at 60+: {sum(late) / max(1, len(late)):.3f} "
            f"(turns {len(late)})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
