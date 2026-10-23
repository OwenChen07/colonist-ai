"""E75 follow-up: is the bot's robber aim worse than people's once the choice
counts its own buildings and what the hex's resource means to the opponent?
Over the 2100+ ranked games with converted captures (same set as e75_robber.py),
at every robber move by either side, over the legal hexes:
  net    = opponent pips blocked minus own pips blocked (cities double)
  dep    = for the opponent, the share of its income of that hex's resource the
           hex carries (1.0 = its only source), times the opponent pips there
  scarce = opponent pips weighted by how rare the resource is on this board
For each score: whether the move took the best legal hex, and what share of
the best it reached. Plus own pips blocked and moves onto a hex with none of
the opponent's pips. By side, outcome and move turn; game-clustered bootstrap.

    .venv/bin/python data/orchestrator/scratch/live-loss/e75_aim.py [WORKERS]
    .venv/bin/python data/orchestrator/scratch/live-loss/e75_aim.py table
"""

import json
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import jobs, pips, col  # noqa: E402

OUT = "data/orchestrator/live-loss/e75_aim.json"
SCORES = ("net", "dep", "scarce")


def per_hex(board):
    """{coord: (resource, pips)} and {color: {coord: pips}} for number hexes"""
    hexes, own = {}, {}
    for coord, tile in board.map.land_tiles.items():
        p = pips(tile.number)
        if not p:
            continue
        hexes[coord] = (tile.resource, p)
        for node in tile.nodes.values():
            b = board.buildings.get(node)
            if b:
                d = own.setdefault(col(b[0]), {})
                d[coord] = d.get(coord, 0) + p * (2 if "CITY" in str(b[1]) else 1)
    return hexes, own


def scores(hexes, own, me, opp, coords):
    o, m = own.get(opp, {}), own.get(me, {})
    by_res = {}
    for c, v in o.items():
        r = hexes[c][0]
        by_res[r] = by_res.get(r, 0) + v
    board_res = {}
    for r, p in hexes.values():
        board_res[r] = board_res.get(r, 0) + p
    mean_res = sum(board_res.values()) / max(1, len(board_res))
    out = {}
    for c in coords:
        ov, mv = o.get(c, 0), m.get(c, 0)
        r = hexes[c][0] if c in hexes else None
        dep = ov * ov / by_res[r] if ov else 0.0
        scarce = ov * mean_res / board_res[r] if ov else 0.0
        # own pips count against every score, as in "net"
        out[c] = {
            "net": ov - mv,
            "dep": dep - mv,
            "scarce": scarce - mv,
            "o": ov,
            "m": mv,
        }
    return out


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.moves = []

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase or decision.action_type != ActionType.MOVE_ROBBER:
            return
        me = col(decision.color)
        if me not in self.side:
            return
        coords = {
            a.value[0]
            for a in game.playable_actions
            if a.action_type == ActionType.MOVE_ROBBER
        }
        chosen = decision.value[0]
        coords.add(chosen)
        hexes, own = per_hex(s.board)
        sc = scores(hexes, own, me, self.other[me], coords)
        ch = sc[chosen]
        row = {"side": self.side[me], "turn": s.num_turns, "o": ch["o"], "m": ch["m"]}
        for k in SCORES:
            best = max(v[k] for v in sc.values())
            row[k + "_best"] = ch[k] >= best - 1e-9
            row[k + "_frac"] = ch[k] / best if best > 0 else 1.0
        row["clean_any"] = any(v["m"] == 0 and v["o"] > 0 for v in sc.values())
        self.moves.append(row)


def facts(job):
    path, won, rolls = job
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors or not obs.moves:
            return None
        return {"won": won, "rolls": rolls, "moves": obs.moves}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(groups, f, n=1000):
    rng = random.Random(1)
    groups = [g for g in groups if g]

    def mean(gs):
        return sum(f(x) for g in gs for x in g) / max(1, sum(len(g) for g in gs))

    m = mean(groups)
    bs = sorted(mean(rng.choices(groups, k=len(groups))) for _ in range(n))
    return m, bs[int(0.025 * n)], bs[int(0.975 * n)]


def gap(gb, gp, f, n=1000):
    """bot's mean minus the person's, independent game resamples"""
    rng = random.Random(2)
    gb, gp = [g for g in gb if g], [g for g in gp if g]

    def mean(gs):
        return sum(f(x) for g in gs for x in g) / max(1, sum(len(g) for g in gs))

    m = mean(gb) - mean(gp)
    bs = sorted(
        mean(rng.choices(gb, k=len(gb))) - mean(rng.choices(gp, k=len(gp)))
        for _ in range(n)
    )
    return m, bs[int(0.025 * n)], bs[int(0.975 * n)]


def fmt(t, pct=True):
    if pct:
        return f"{100 * t[0]:.1f}% [{100 * t[1]:.1f}, {100 * t[2]:.1f}]"
    return f"{t[0]:.2f} [{t[1]:.2f}, {t[2]:.2f}]"


METRICS = (
    ("took best net", lambda m: float(m["net_best"]), True),
    ("took best dep", lambda m: float(m["dep_best"]), True),
    ("took best scarce", lambda m: float(m["scarce_best"]), True),
    ("share of best net", lambda m: m["net_frac"], True),
    ("share of best dep", lambda m: m["dep_frac"], True),
    ("own pips blocked", lambda m: m["m"], False),
    ("opp pips blocked", lambda m: m["o"], False),
    (
        "on own pips though a clean hex existed",
        lambda m: float(m["m"] > 0 and m["clean_any"]),
        True,
    ),
)


def table(res):
    print(f"{len(res)} games, {sum(len(r['moves']) for r in res)} robber moves")
    turns = (
        ("all turns", 0, 999),
        ("turns <30", 0, 30),
        ("turns 30-60", 30, 60),
        ("turns 60+", 60, 999),
    )
    subsets = (
        ("all games", res),
        ("bot lost", [r for r in res if not r["won"]]),
        ("bot won", [r for r in res if r["won"]]),
        ("90+ rolls", [r for r in res if r["rolls"] >= 90]),
    )
    for sname, sel in subsets:
        for tname, lo, hi in turns:
            gb = [
                [m for m in r["moves"] if m["side"] == "bot" and lo <= m["turn"] < hi]
                for r in sel
            ]
            gp = [
                [
                    m
                    for m in r["moves"]
                    if m["side"] == "person" and lo <= m["turn"] < hi
                ]
                for r in sel
            ]
            nb, np_ = sum(map(len, gb)), sum(map(len, gp))
            print(
                f"== {sname}, {tname}: {len(sel)} games, bot {nb} moves, person {np_} moves"
            )
            for name, f, pct in METRICS:
                print(
                    f"  {name:40} bot {fmt(boot(gb, f), pct):24} person {fmt(boot(gp, f), pct):24}"
                    f" gap {fmt(gap(gb, gp, f), pct)}"
                )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2:
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        if len(sys.argv) <= 2:
            with open(OUT, "w") as f:
                json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
