"""Skipped-port positions (bank_trade.py follow-up). Bot turns after setup in
2100+ ranked games (e75_robber.py's games) where a settlement on a port node
was legal and the bot built no port settlement that turn. The position is the
turn's first bot decision with a port settlement legal. One a game, SIZE from
lost games and SIZE from won games.

Playouts as in forced_settle.py: arm 'port' plays the best port settlement
(2:1 first, then pips), arm 'bot' the bot's real move; both sides then play
on with NetPlayer (SIMS; 0 = policy argmax), GAMES seeded games per arm.

    build [WORKERS]                       -> live-loss/port_set.jsonl
    run NET SIMS GAMES [LIMIT] [WORKERS]  -> port_set_forced_<net>_<sims>.jsonl
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
from e75_robber import jobs, col  # noqa: E402
from forced_settle import playout  # noqa: E402
from robber_set import players, boot  # noqa: E402
from room2 import node_pips  # noqa: E402
from settle_set import S  # noqa: E402
from catanatron.state_functions import (  # noqa: E402
    get_actual_victory_points,
    get_visible_victory_points,
)

OUT = "data/orchestrator/live-loss/port_set.jsonl"
SIZE = 100


def ports(board):
    """node -> 2 for a 2:1 port, 1 for 3:1."""
    out = {}
    for res, nodes in board.map.port_nodes.items():
        for n in nodes:
            out[n] = 2 if res is not None else 1
    return out


def port_settles(game):
    p = ports(game.state.board)
    return [a for a in game.playable_actions if a.action_type == S and a.value in p], p


class Obs:
    def __init__(self, you, them, target=None):
        self.you, self.them, self.target = you, them, target
        self.k = -1
        self.turns = {}
        self.game = None
        self.move = None
        self._p = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase or col(d.color) != self.you:
            return
        if self._p is None:
            self._p = ports(s.board)
        row = self.turns.get(s.num_turns)
        if row is not None and d.action_type == S:
            row["settled"] = True
            if d.value in self._p:
                row["port"] = True
        cand, p = port_settles(game)
        if not cand:
            return
        self.k += 1
        if self.target is not None:
            if self.k == self.target:
                self.game = game.copy()
                self.move = d
            return
        if row is None:
            best = max(cand, key=lambda a: (p[a.value], node_pips(s.board, a.value)))
            self.turns[s.num_turns] = {
                "k": self.k,
                "turn": s.num_turns,
                "kind": p[best.value],
                "pips": node_pips(s.board, best.value),
                "other": max(
                    [
                        node_pips(s.board, a.value)
                        for a in game.playable_actions
                        if a.action_type == S
                    ],
                ),
                "first": d.action_type.value,
                "settled": d.action_type == S,
                "port": d.action_type == S and d.value in p,
                "vp": get_actual_victory_points(s, d.color),
                "ovp": get_visible_victory_points(
                    s, next(c for c in s.colors if c != d.color)
                ),
            }


def find(job):
    path, won, rolls = job
    try:
        record, you, them = players(path)
        if record is None:
            return []
        obs = Obs(you, them)
        if replay(record, observer=obs).errors:
            return []
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return []
    cand = [r for r in obs.turns.values() if not r["port"]]
    took = sum(r["port"] for r in obs.turns.values())
    if not cand:
        return [{"capture": path, "won": won, "took": took, "none": True}]
    r = random.Random(path).choice(cand)
    return [
        dict(r, capture=path, won=won, rolls=rolls, took=took, turns=len(obs.turns))
    ]


def build(workers):
    todo = jobs()
    print(f"{len(todo)} games", flush=True)
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        rows = [r for part in pool.map(find, todo, chunksize=8) for r in part]
    picked = []
    for won in (False, True):
        g = [r for r in rows if r["won"] == won]
        c = [r for r in g if not r.get("none")]
        print(
            f"won={won}: {len(g)} games with a port settle legal after setup;"
            f" {len(c)} with a skipped one; turns with one legal mean"
            f" {np.mean([r['turns'] for r in c]):.1f}, took a port in"
            f" {np.mean([r['took'] > 0 for r in g]):.0%} of games"
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
            f"  won={won}: {len(p)}; 2:1 {np.mean([r['kind'] == 2 for r in p]):.0%};"
            f" port pips {np.mean([r['pips'] for r in p]):.1f} vs best spot {np.mean([r['other'] for r in p]):.1f};"
            f" bot built elsewhere {np.mean([r['settled'] for r in p]):.0%};"
            f" turn median {int(np.median([r['turn'] for r in p]))}"
        )


def one(args):
    r, net, sims, games = args
    try:
        record, you, them = players(r["capture"])
        obs = Obs(you, them, target=r["k"])
        replay(record, observer=obs)
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None
    game = obs.game
    if game is None or obs.move is None:
        return None
    cand, p = port_settles(game)
    best = max(cand, key=lambda a: (p[a.value], node_pips(game.state.board, a.value)))
    real = obs.move
    t0 = time.time()
    base = zlib.crc32(f"{r['capture']}:{r['k']}".encode())
    res = {"port": [], "bot": []}
    for i in range(games):
        for arm, first in (("port", best), ("bot", real)):
            res[arm].append(playout(game, first, base + i, net, sims))
    return {
        "capture": r["capture"],
        "k": r["k"],
        "won": r["won"],
        "kind": r["kind"],
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
    with (
        Pool(workers, initializer=os.nice, initargs=(19,)) as pool,
        open(out, "w") as f,
    ):
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
        ("2:1", lambda r: r["kind"] == 2),
        ("3:1", lambda r: r["kind"] == 1),
    ]
    for t in sorted({r["real"] for r in rows}):
        splits.append((f"real={t}", lambda r, t=t: r["real"] == t))
    for label, keep in splits:
        ws, wb, groups, nd = [], [], [], 0
        for r in rows:
            if not keep(r):
                continue
            s = [x[0] for x in r["res"]["port"] if x[0] is not None]
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
            f"  {label}: {len(ws)} positions; win port {100 * np.mean(ws):.1f}% vs bot move"
            f" {100 * np.mean(wb):.1f}%; diff {100 * m:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}];"
            f" port better in {sum(a > b for a, b in zip(ws, wb))}, worse in"
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
