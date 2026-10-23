"""Road direction toward ports, bot vs person, over the 2100+ ranked games with
converted captures. For each road built after setup in turns 20-70: does it
shorten the board distance from the builder's network to the nearest open
port node (a port node still buildable)? Baseline: does it shorten the
distance to the nearest open buildable node of any kind? Also how soon each
side owns its first port after setup. By outcome, game-clustered bootstrap
95% ranges. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/road_port.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/road_port.py table
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
from catanatron.models.board import get_node_distances  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/road_port_games.json"
FIELDS = (
    "roads",
    "to_port",
    "to_spot",
    "roads_np",
    "to_port_np",
    "near_np",
    "to_port_near",
    "one",
    "port_by40",
    "port_ever",
    "port_turn_sum",
    "noport_start",
)
LO, HI = 20, 70
DIST = None


def dist(nodes, targets):
    if not nodes or not targets:
        return 99
    return min(DIST[a][b] for a in nodes for b in targets)


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.sums = {s: dict.fromkeys(FIELDS, 0) for s in ("bot", "person")}
        self.started = {}
        for t in self.sums.values():
            t["one"] = 1

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        me = col(decision.color)
        side = self.side.get(me)
        if side is None:
            return
        t = self.sums[side]
        b = s.board
        a = decision.action_type
        if a == ActionType.ROLL:
            has = bool(b.get_player_port_resources(decision.color))
            if side not in self.started:
                self.started[side] = has
                t["noport_start"] = int(not has)
            if not self.started[side] and has and not t["port_ever"]:
                t["port_ever"] = 1
                t["port_turn_sum"] = s.num_turns
                t["port_by40"] = int(s.num_turns <= 40)
        elif a == ActionType.BUILD_ROAD and LO <= s.num_turns < HI:
            net = set().union(*b.connected_components[decision.color])
            open_ids = b.board_buildable_ids
            ports = {n for ns in b.map.port_nodes.values() for n in ns} & open_ids
            new = set(decision.value)
            dp0, ds0 = dist(net, ports), dist(net, open_ids)
            dp1 = min(dp0, dist(new, ports))
            ds1 = min(ds0, dist(new, open_ids))
            t["roads"] += 1
            t["to_port"] += dp1 < dp0
            t["to_spot"] += ds1 < ds0
            if not b.get_player_port_resources(decision.color):
                t["roads_np"] += 1
                t["to_port_np"] += dp1 < dp0
                if dp0 <= 2:
                    t["near_np"] += 1
                    t["to_port_near"] += dp1 < dp0


def facts(job):
    global DIST
    if DIST is None:
        DIST = get_node_distances()
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
        if rep.errors or "bot" not in obs.started:
            if sys.argv[-1] == "debug":
                print("skip:", rep.errors[:2], obs.sums["bot"], flush=True)
            return None
        return {"won": won, "rolls": rolls, **obs.sums}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def ratio(side, num, den, scale=1.0):
    def f(gs):
        d = sum(g[side][den] for g in gs)
        return scale * sum(g[side][num] for g in gs) / d if d else float("nan")

    return f


def boot(gs, f, n=1000):
    rng = random.Random(1)
    if not gs:
        return float("nan"), float("nan"), float("nan")
    bs = sorted(f(rng.choices(gs, k=len(gs))) for _ in range(n))
    return f(gs), bs[25], bs[975]


def fmt(t, d=3):
    return f"{t[0]:.{d}f} [{t[1]:.{d}f}, {t[2]:.{d}f}]"


ROWS = (
    ("roads/game 20-70", "roads", "one", 1),
    ("toward open port", "to_port", "roads", 1),
    ("toward open spot", "to_spot", "roads", 1),
    ("toward port, no port", "to_port_np", "roads_np", 1),
    ("same, port <=2 away", "to_port_near", "near_np", 1),
    ("no port after setup", "noport_start", "one", 1),
    ("first port by t40", "port_by40", "noport_start", 1),
    ("first port ever", "port_ever", "noport_start", 1),
    ("turn of first port", "port_turn_sum", "port_ever", 1),
)


def table(res):
    for outcome, gs in (
        ("bot lost", [r for r in res if not r["won"]]),
        ("bot won", [r for r in res if r["won"]]),
        ("all", res),
    ):
        print(f"== {outcome}: {len(gs)} games")
        for label, num, den, sc in ROWS:
            b = boot(gs, ratio("bot", num, den, sc))
            p = boot(gs, ratio("person", num, den, sc))
            print(f"  {label:22} bot {fmt(b)}   person {fmt(p)}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2 and sys.argv[2] != "debug":
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
