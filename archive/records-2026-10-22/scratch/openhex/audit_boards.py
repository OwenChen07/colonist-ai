"""Audit item 1: colonist boards (captures) vs our self-play generator.

Usage: audit_boards.py [N_SELFPLAY]
"""

import collections
import glob
import json
import random
import sys

from catanatron.colonist.convert import convert_map, game_start, ConvertError
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.rules import new_game
from catanatron.models.map import LandTile, Port, CatanMap
from catanatron.models.player import RandomPlayer, Color
from catanatron.serialization import map_from_json

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
DIRS = [(1, -1, 0), (1, 0, -1), (0, 1, -1), (-1, 1, 0), (-1, 0, 1), (0, -1, 1)]


def ring(c):
    return max(abs(x) for x in c)


def stats(m):
    land = {c: t for c, t in m.tiles.items() if isinstance(t, LandTile)}
    s = {}
    desert = [c for c, t in land.items() if t.resource is None]
    s["n_desert"] = len(desert)
    s["desert_ring"] = ring(desert[0]) if desert else None
    s["res_counts"] = tuple(
        sorted(collections.Counter(str(t.resource) for t in land.values()).items())
    )
    s["numbers"] = tuple(sorted(t.number for t in land.values() if t.number))
    red, same, sameres = 0, 0, 0
    for c, t in land.items():
        for d in DIRS:
            n = tuple(a + b for a, b in zip(c, d))
            if n <= c or n not in land:
                continue
            u = land[n]
            if t.number in (6, 8) and u.number in (6, 8):
                red += 1
            if t.number and t.number == u.number:
                same += 1
            if t.resource and t.resource == u.resource:
                sameres += 1
    s["red_adj"] = red
    s["same_num_adj"] = same
    s["same_res_adj"] = sameres
    s["red_rings"] = tuple(
        sorted(ring(c) for c, t in land.items() if t.number in (6, 8))
    )
    pips = collections.Counter()
    for t in land.values():
        if t.number:
            pips[str(t.resource)] += PIPS[t.number]
    s["pips"] = dict(pips)
    # port types by template position (coordinate)
    s["ports"] = {c: str(t.resource) for c, t in m.tiles.items() if isinstance(t, Port)}
    # number on each ring
    s["ring_pips"] = tuple(
        sum(PIPS[t.number] for c, t in land.items() if t.number and ring(c) == r)
        for r in (0, 1, 2)
    )
    return s


def colonist_maps():
    seen = set()
    errors = collections.Counter()
    for p in sorted(glob.glob("captures/*/bridge-*.json")):
        try:
            frames = load_capture(p)
            f = game_start(frames)
        except Exception as e:  # noqa
            errors["load: " + type(e).__name__] += 1
            continue
        if f is None:
            errors["no start"] += 1
            continue
        payload = f.data["data"]["payload"]
        gid = (payload.get("gameSettings") or {}).get("id")
        if gid in seen:
            errors["duplicate game"] += 1
            continue
        seen.add(gid)
        try:
            doc = convert_map(payload["gameState"]["mapState"])
        except (ConvertError, KeyError) as e:
            errors["convert: " + str(e)[:60]] += 1
            continue
        yield map_from_json(doc), payload
    print("colonist skipped:", dict(errors))


def selfplay_maps(n):
    for seed in range(n):
        g = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed)
        yield g.state.board.map


def summarize(name, rows):
    n = len(rows)
    print(f"\n=== {name}: {n} boards")
    for key in ("n_desert", "desert_ring", "red_adj", "same_num_adj", "same_res_adj"):
        c = collections.Counter(r[key] for r in rows)
        print(
            f"{key}: "
            + ", ".join(f"{k}:{v/n:.3f}" for k, v in sorted(c.items(), key=str))
        )
    c = collections.Counter(r["res_counts"] for r in rows)
    print("res_counts:", c.most_common(3))
    c = collections.Counter(r["numbers"] for r in rows)
    print("numbers distinct multisets:", len(c), c.most_common(1)[0][1] / n)
    c = collections.Counter(r["red_rings"] for r in rows)
    print("red rings:", ", ".join(f"{k}:{v/n:.3f}" for k, v in c.most_common(6)))
    rp = [r["ring_pips"] for r in rows]
    print(
        "mean pips by ring 0/1/2:",
        [round(sum(x[i] for x in rp) / n, 2) for i in range(3)],
    )
    maxp = collections.Counter(max(r["pips"].values()) // 2 * 2 for r in rows)
    print(
        "max resource pips (binned 2):",
        sorted((k, round(v / n, 3)) for k, v in maxp.items()),
    )
    for res in ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE"):
        vals = [r["pips"].get(res, 0) for r in rows]
        mean = sum(vals) / n
        sd = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
        print(f"  pips {res}: mean {mean:.2f} sd {sd:.2f}")
    pc = collections.Counter()
    for r in rows:
        pc.update(r["ports"].values())
    print("port types:", {k: round(v / n, 3) for k, v in sorted(pc.items())})
    pos = collections.defaultdict(collections.Counter)
    for r in rows:
        for c, t in r["ports"].items():
            pos[c][t] += 1
    worst = max(abs(cnt["None"] / n - 4 / 9) for cnt in pos.values())
    print("3:1 port share per position, worst deviation from 4/9:", round(worst, 3))


def main():
    n_self = int(sys.argv[1]) if len(sys.argv) > 1 else 4700
    col = [stats(m) for m, _ in colonist_maps()]
    summarize("colonist captures", col)
    sp = [stats(m) for m in selfplay_maps(n_self)]
    summarize("self-play generator", sp)


if __name__ == "__main__":
    main()
