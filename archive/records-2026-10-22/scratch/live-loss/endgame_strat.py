"""endgame_set per game, stratified to control for game length.

Writes data/orchestrator/live-loss/endgame_games.jsonl (per game: path, won,
turns with a position, first-position followed, any miss) and prints win
rates (Wilson 95%) by arm within strata of qualifying-turn count, plus a
first-opportunity comparison (followed vs missed at the first position).
"""

import glob
import json
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "catanatron"))

from catanatron.colonist.endgame_set import _wilson, scan  # noqa: E402

OUT = Path("data/orchestrator/live-loss/endgame_games.jsonl")


def one(path):
    try:
        res = scan(path)
    except (OSError, ValueError, KeyError, IndexError):
        return None
    if res is None:
        return None
    rows, won = res
    if not rows:
        return None
    turns = sorted({r["turn"] for r in rows})
    first = [r for r in rows if r["turn"] == turns[0]]
    return {
        "path": path,
        "won": bool(won),
        "qual_turns": len(turns),
        "first_turn": turns[0],
        "first_followed": all(r["followed"] for r in first),
        "miss": any(not r["followed"] for r in rows),
    }


def fmt(w, n):
    if not n:
        return "  -"
    p, lo, hi = _wilson(w, n)
    return f"{w}/{n} = {p:.1%} [{lo:.1%}, {hi:.1%}]"


def main():
    paths = []
    for b in ("bot3", "bot4", "bot5", "bot7"):
        paths += sorted(glob.glob(f"captures/{b}/bridge-202610*.json"))
    with Pool(6) as pool:
        games = [g for g in pool.map(one, paths, chunksize=8) if g]
    with OUT.open("w") as f:
        for g in games:
            f.write(json.dumps(g) + "\n")
    print(f"{len(paths)} captures, {len(games)} games with a position")

    print("\nall-turns arm (followed every time vs >=1 miss):")
    for name, lo, hi in (
        ("1 turn", 1, 1),
        ("2 turns", 2, 2),
        ("3-4", 3, 4),
        ("5+", 5, 999),
        ("all", 1, 999),
    ):
        gs = [g for g in games if lo <= g["qual_turns"] <= hi]
        fol = [g for g in gs if not g["miss"]]
        mis = [g for g in gs if g["miss"]]
        print(
            f"  {name:8s} followed {fmt(sum(g['won'] for g in fol), len(fol)):32s} miss {fmt(sum(g['won'] for g in mis), len(mis))}"
        )

    print("\nfirst opportunity only (followed vs missed at first qualifying turn):")
    for name, lo, hi in (("1 turn", 1, 1), ("2+", 2, 999), ("all", 1, 999)):
        gs = [g for g in games if lo <= g["qual_turns"] <= hi]
        fol = [g for g in gs if g["first_followed"]]
        mis = [g for g in gs if not g["first_followed"]]
        print(
            f"  {name:8s} followed {fmt(sum(g['won'] for g in fol), len(fol)):32s} miss {fmt(sum(g['won'] for g in mis), len(mis))}"
        )


if __name__ == "__main__":
    main()
