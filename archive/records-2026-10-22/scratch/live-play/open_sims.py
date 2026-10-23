"""Does the opening settlement gain from more simulations? For setup
settlement positions from fresh 1v1 boards: two 20k searches (different seeds)
and one big search (BIG sims). Agreement and the big search's Q loss of each
20k choice. Usage: open_sims.py NET GAMES WORKERS BIG [SMALL]"""

import json
import multiprocessing
import random
import sys
import time

import numpy as np

from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.rules import new_game
from catanatron.models.player import Color, RandomPlayer

NET_PATH = sys.argv[1]
GAMES = int(sys.argv[2])
WORKERS = int(sys.argv[3])
BIG = int(sys.argv[4])
SMALL = int(sys.argv[5]) if len(sys.argv) > 5 else 20000
_net = None


def net():
    global _net
    if _net is None:
        _net = Net.load(NET_PATH)
    return _net


def positions(seed):
    game = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed)
    out = []
    rng = random.Random(seed)
    while game.state.is_initial_build_phase:
        if game.state.current_prompt.name == "BUILD_INITIAL_SETTLEMENT":
            out.append((seed, len(out), game.copy()))
        s = Search(net(), 400, rng=rng, noise=False)
        action, _ = s.run(game)
        game.execute(action)
    return out


def study(item):
    seed, k, game = item
    res = {"seed": seed, "k": k}
    roots = {}
    for name, sims, rs in (("a", SMALL, 1), ("b", SMALL, 2), ("c", BIG, 3)):
        s = Search(net(), sims, rng=random.Random(seed * 10 + rs), noise=False)
        t = time.time()
        action, root = s.run(game)
        res[name + "_s"] = round(time.time() - t, 1)
        res[name] = root.actions.index(action)
        roots[name] = (s, root)
    s, root = roots["c"]
    q = np.asarray(s.completed_q(root))
    res["n"] = len(root.actions)
    res["loss_a"] = float(q[res["c"]] - q[res["a"]])
    res["loss_b"] = float(q[res["c"]] - q[res["b"]])
    res["q_best"] = float(q[res["c"]])
    print(json.dumps(res), flush=True)
    return res


if __name__ == "__main__":
    with multiprocessing.Pool(WORKERS) as pool:
        items = [p for ps in pool.map(positions, range(GAMES)) for p in ps]
        print(f"{len(items)} positions", flush=True)
        rows = pool.map(study, items, chunksize=1)
    n = len(rows)
    ab = sum(r["a"] == r["b"] for r in rows)
    ac = sum(r["a"] == r["c"] for r in rows) + sum(r["b"] == r["c"] for r in rows)
    la = [r["loss_a"] for r in rows] + [r["loss_b"] for r in rows]
    print(f"positions {n}; {SMALL} vs {SMALL} agree {ab}/{n}")
    print(f"{SMALL} vs {BIG} agree {ac}/{2 * n}")
    print(
        f"Q loss of a {SMALL} choice under the {BIG} search: mean {np.mean(la):.4f},"
        f" max {np.max(la):.4f}, share over 0.01 {np.mean(np.array(la) > 0.01):.2f}"
    )
