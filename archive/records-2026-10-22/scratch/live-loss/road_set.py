"""Port-ward road playouts (road_port.py follow-up). Bot decisions in turns
20-70 of 2100+ ranked games where the bot holds no port, some legal road
shortens the distance from its network to an open port node, and the bot
built a road that does not, or bought a development card. One a game, SIZE
from lost games and SIZE from won games.

Arm 'road' plays the port-ward road (shortest new distance, ties by the pips
of the nearest open port node); arm 'bot' the bot's real move; both sides
then play on with NetPlayer (SIMS; 0 = policy argmax), GAMES seeded games.

    build [WORKERS]                       -> live-loss/road_set.jsonl
    run NET SIMS GAMES [LIMIT] [WORKERS]  -> road_set_forced_<net>_<sims>.jsonl
    sum FILE
"""

import json
import os
import random
import sys
import time
import zlib
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.board import get_node_distances  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import jobs, col  # noqa: E402
from forced_settle import playout  # noqa: E402
from robber_set import players, boot  # noqa: E402
from room2 import node_pips  # noqa: E402

OUT = "data/orchestrator/live-loss/road_set.jsonl"
SIZE = 100
LO, HI = 20, 70
R, D = ActionType.BUILD_ROAD, ActionType.BUY_DEVELOPMENT_CARD
DIST = None


def dist(nodes, targets):
    if not nodes or not targets:
        return 99
    return min(DIST[a][b] for a in nodes for b in targets)


def best_road(game):
    """(port-ward road, its distance, current distance) or None."""
    b = game.state.board
    color = game.state.current_color()
    if b.get_player_port_resources(color):
        return None
    roads = [a for a in game.playable_actions if a.action_type == R]
    if not roads:
        return None
    ports = {n for ns in b.map.port_nodes.values() for n in ns} & b.board_buildable_ids
    if not ports:
        return None
    net = set().union(*b.connected_components[color])
    d0 = dist(net, ports)

    def key(a):
        d = dist(set(a.value), ports)
        near = [p for p in ports if min(DIST[n][p] for n in a.value) == d]
        return (d, -max(node_pips(b, p) for p in near))

    best = min(roads, key=key)
    d1 = key(best)[0]
    return (best, d1, d0) if d1 < d0 else None


class Obs:
    def __init__(self, you, target=None):
        self.you, self.target = you, target
        self.k = -1
        self.rows = []
        self.game = None
        self.move = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase or col(d.color) != self.you:
            return
        if not (LO <= s.num_turns < HI) or d.action_type not in (R, D):
            return
        got = best_road(game)
        if got is None:
            return
        best, d1, d0 = got
        if d.action_type == R:
            ports = {n for ns in s.board.map.port_nodes.values() for n in ns}
            ports &= s.board.board_buildable_ids
            if dist(set(d.value), ports) < d0:
                return
        self.k += 1
        if self.target is not None:
            if self.k == self.target:
                self.game = game.copy()
                self.move = d
            return
        self.rows.append(
            {
                "k": self.k,
                "turn": s.num_turns,
                "real": d.action_type.value,
                "d0": d0,
                "d1": d1,
            }
        )


def init():
    global DIST
    os.nice(19)
    DIST = get_node_distances()


def find(job):
    path, won, rolls = job
    try:
        record, you, them = players(path)
        if record is None:
            return []
        obs = Obs(you)
        if replay(record, observer=obs).errors:
            return []
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return []
    if not obs.rows:
        return [{"capture": path, "won": won, "none": True}]
    r = random.Random(path).choice(obs.rows)
    return [dict(r, capture=path, won=won, rolls=rolls, n=len(obs.rows))]


def build(workers):
    todo = jobs()
    print(f"{len(todo)} games", flush=True)
    with Pool(workers, initializer=init) as pool:
        rows = [r for part in pool.map(find, todo, chunksize=8) for r in part]
    picked = []
    for won in (False, True):
        g = [r for r in rows if r["won"] == won]
        c = [r for r in g if not r.get("none")]
        print(
            f"won={won}: {len(g)} games replayed; {len(c)} with a skipped port-ward road;"
            f" such decisions a game mean {np.mean([r['n'] for r in c]):.1f}"
        )
        random.Random(77).shuffle(c)
        picked += c[:SIZE]
    picked.sort(key=lambda r: (r["capture"], r["k"]))
    with open(OUT, "w") as f:
        for r in picked:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(picked)} positions to {OUT}")
    for won in (False, True):
        p = [r for r in picked if r["won"] == won]
        print(
            f"  won={won}: {len(p)}; bot bought a dev card {np.mean([r['real'] == D.value for r in p]):.0%};"
            f" distance {np.mean([r['d0'] for r in p]):.2f} -> {np.mean([r['d1'] for r in p]):.2f};"
            f" turn median {int(np.median([r['turn'] for r in p]))}"
        )


def one(args):
    r, net, sims, games = args
    try:
        record, you, them = players(r["capture"])
        obs = Obs(you, target=r["k"])
        replay(record, observer=obs)
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None
    game = obs.game
    if game is None or obs.move is None:
        return None
    best = best_road(game)[0]
    real = obs.move
    t0 = time.time()
    base = zlib.crc32(f"{r['capture']}:{r['k']}".encode())
    res = {"road": [], "bot": []}
    for i in range(games):
        for arm, first in (("road", best), ("bot", real)):
            res[arm].append(playout(game, first, base + i, net, sims))
    return {
        "capture": r["capture"],
        "k": r["k"],
        "won": r["won"],
        "real": real.action_type.value,
        "turn0": game.state.num_turns,
        "res": res,
        "secs": round(time.time() - t0, 1),
    }


def run(net, sims, games, limit, workers):
    rows = [json.loads(line) for line in open(OUT)]
    random.Random(5).shuffle(rows)
    rows = rows[:limit]
    out = OUT.replace(".jsonl", f"_forced_{os.path.basename(net)}_{sims}.jsonl")
    todo = [(r, net, sims, games) for r in rows]
    n = 0
    with Pool(workers, initializer=init) as pool, open(out, "w") as f:
        for x in pool.imap_unordered(one, todo):
            n += 1
            if x is not None:
                f.write(json.dumps(x) + "\n")
                f.flush()
            print(f"done {n}/{len(todo)}", flush=True)
    print(f"wrote {out}")
    summary(out)


def summary(path):
    rows = [json.loads(line) for line in open(path)]
    print(
        f"{path}: {len(rows)} positions, {np.mean([r['secs'] for r in rows]):.0f} s each"
    )
    splits = [
        ("all", lambda r: True),
        ("lost", lambda r: not r["won"]),
        ("won", lambda r: r["won"]),
    ]
    for t in sorted({r["real"] for r in rows}):
        splits.append((f"real={t}", lambda r, t=t: r["real"] == t))
    for label, keep in splits:
        ws, wb, groups, nd = [], [], [], 0
        for r in rows:
            if not keep(r):
                continue
            s = [x[0] for x in r["res"]["road"] if x[0] is not None]
            b = [x[0] for x in r["res"]["bot"] if x[0] is not None]
            nd += sum(x[0] is None for a in r["res"].values() for x in a)
            if not s or not b:
                continue
            ws.append(np.mean(s))
            wb.append(np.mean(b))
            groups.append([(np.mean(s) - np.mean(b), 0)])
        if not ws:
            continue
        m, lo, hi = boot(groups, 0)
        print(
            f"  {label}: {len(ws)} positions; win road {100 * np.mean(ws):.1f}% vs bot move"
            f" {100 * np.mean(wb):.1f}%; diff {100 * m:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}];"
            f" road better in {sum(a > b for a, b in zip(ws, wb))}, worse in"
            f" {sum(a < b for a, b in zip(ws, wb))}; unfinished {nd}"
        )


def main():
    cmd = sys.argv[1]
    if cmd == "sum":
        summary(sys.argv[2])
    elif cmd == "build":
        build(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        net, sims, games = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 200
        workers = int(sys.argv[6]) if len(sys.argv) > 6 else 3
        run(net, sims, games, limit, workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
