"""Where late roads go, bot vs person, games of 70+ rolls vs 2100+. For each road
built after setup, by phase (rolls 40-59 and 60+): does it
  spot    shorten the distance from the builder's network to an open settlement spot,
  contest ... to a spot that is also within 2 of the opponent's network,
  block   take an edge the opponent could have built,
  cut     take an edge that would have lengthened the opponent's longest road,
  length  lengthen the builder's own longest road.
Exclusive class: spot > cut > block > length > none. Per 10 own turns and shares,
by outcome, game-clustered bootstrap 95%. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/road_where.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/road_where.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.board import get_node_distances  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from long_games import boot, mean  # noqa: E402

OUT = "data/orchestrator/live-loss/road_where.json"
FLAGS = ("spot", "contest", "block", "cut", "length")
CLASSES = ("c_spot", "c_cut", "c_block", "c_length", "c_none")
PHASES = ("mid", "late")
DIST = None


def dist(nodes, targets):
    if not nodes or not targets:
        return 99
    return min(DIST[a][b] for a in nodes for b in targets)


def edge_in(edge, edges):
    return edge in edges or (edge[1], edge[0]) in edges


def gain(b, color, edge):
    if not edge_in(edge, b.buildable_edges(color)):
        return 0
    b2 = b.copy()
    before = b2.road_lengths[color]
    b2.build_road(color, edge)
    return b2.road_lengths[color] - before


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.rolls = 0
        self.t = {
            sd: {
                ph: {"turns": 0, "roads": 0, **dict.fromkeys(FLAGS + CLASSES, 0)}
                for ph in PHASES
            }
            for sd in ("bot", "person")
        }

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        at = decision.action_type
        if at == ActionType.ROLL:
            self.rolls += 1
        if self.rolls < 40:
            return
        me = col(decision.color)
        sd = self.side.get(me)
        if not sd:
            return
        t = self.t[sd]["mid" if self.rolls < 60 else "late"]
        if at == ActionType.END_TURN:
            t["turns"] += 1
            return
        if at != ActionType.BUILD_ROAD:
            return
        b = s.board
        edge = tuple(decision.value)
        opp = next(c for c in b.connected_components if col(c) == self.other[me])
        net = set().union(*b.connected_components[decision.color])
        onet = set().union(*b.connected_components[opp])
        open_ids = b.board_buildable_ids
        contested = {n for n in open_ids if dist(onet, [n]) <= 2}
        new = set(edge)
        f = {
            "spot": min(dist(net, open_ids), dist(new, open_ids)) < dist(net, open_ids),
            "contest": min(dist(net, contested), dist(new, contested))
            < dist(net, contested),
            "block": edge_in(edge, b.buildable_edges(opp)),
            "cut": gain(b, opp, edge) > 0,
            "length": gain(b, decision.color, edge) > 0,
        }
        t["roads"] += 1
        for k, v in f.items():
            t[k] += bool(v)
        c = next((k for k in ("spot", "cut", "block", "length") if f[k]), "none")
        t["c_" + c] += 1


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
        if rep.errors or obs.rolls < 70:
            return None
        return {"won": won, "rolls": rolls, "t": obs.t}
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        StopIteration,
    ):
        return None


def table(res):
    print(f"{len(res)} games of 70+ rolls")
    for ph in PHASES:
        for won in (False, True):
            sub = [r for r in res if r["won"] == won]
            tag = "bot losses" if not won else "bot wins"
            print(
                f"\n===== {ph} ({'rolls 40-59' if ph == 'mid' else 'roll 60+'}), {tag} ({len(sub)})"
            )
            for who in ("bot", "person"):
                ts = [r["t"][who][ph] for r in sub]
                turns, roads = sum(t["turns"] for t in ts), sum(t["roads"] for t in ts)
                per = " ".join(
                    f"{k} {10 * sum(t[k] for t in ts) / turns:.2f}"
                    for k in ("roads",) + CLASSES
                )
                sh = " ".join(
                    f"{k} {100 * sum(t[k] for t in ts) / roads:.0f}%"
                    for k in FLAGS + CLASSES
                )
                print(f"  {who:6s} {turns} turns, {roads} roads; per 10 turns: {per}")
                print(f"         share of roads: {sh}")
            for k in ("roads",) + CLASSES + ("contest", "cut", "block"):

                def g(rs, k=k):
                    return mean(
                        [
                            10
                            * (
                                r["t"]["bot"][ph][k] / r["t"]["bot"][ph]["turns"]
                                - r["t"]["person"][ph][k]
                                / r["t"]["person"][ph]["turns"]
                            )
                            for r in rs
                            if r["t"]["bot"][ph]["turns"]
                            and r["t"]["person"][ph]["turns"]
                        ]
                    )

                m, a, b = boot(sub, g)
                print(
                    f"  gap {k:8s} per 10 turns (bot - person): {m:+.2f} [{a:+.2f}, {b:+.2f}]"
                )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = [j for j in jobs() if j[2] >= 70]
        if len(sys.argv) > 2:
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
