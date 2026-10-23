"""Forced-settle playouts on settle_set.jsonl. From each position, two arms:
'settle' plays the best-pip settlement on offer, 'bot' plays the bot's real
move from the capture. Both then play on with the net for both sides
(NetPlayer, SIMS simulations; 0 = policy argmax), GAMES seeded playouts per
arm, the same seeds in both arms (fresh dice and dev-card order each seed).

    run NET SIMS GAMES [LIMIT] [WORKERS]  -> forced_settle_<net>_<sims>.jsonl
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
from catanatron.colonist.mcts import NetPlayer  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from robber_set import players, boot  # noqa: E402
from room2 import node_pips  # noqa: E402
from settle_set import Obs, OUT as SET, S  # noqa: E402

MAX_TICKS = 3000


class Obs2(Obs):
    def step(self, game, d):
        k = self.k
        super().step(game, d)
        if self.target is not None and self.k == self.target and k != self.k:
            self.move = d


def playout(game, first, seed, net, sims):
    g = game.copy()
    g.random = g.state.random = random.Random(seed)
    random.seed(seed)
    g.state.random.shuffle(g.state.development_listdeck)
    g.state.players = [
        NetPlayer(p.color, NetPlayer.Params(path=net, simulations=sims, seed=seed))
        for p in g.state.players
    ]
    me = g.state.current_color()
    g.execute(first, validate_action=False)
    ticks = 0
    while g.winning_color() is None and ticks < MAX_TICKS:
        g.play_tick()
        ticks += 1
    w = g.winning_color()
    return None if w is None else w == me, g.state.num_turns


def one(args):
    r, net, sims, games = args
    try:
        record, you, them = players(r["capture"])
        obs = Obs2(you, them, target=r["k"])
        replay(record, observer=obs)
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None
    game = obs.game
    if game is None:
        return None
    settles = [a for a in game.playable_actions if a.action_type == S]
    best = max(settles, key=lambda a: node_pips(game.state.board, a.value))
    real = obs.move
    t0 = time.time()
    base = zlib.crc32(f"{r['capture']}:{r['k']}".encode())
    res = {"settle": [], "bot": []}
    for i in range(games):
        for arm, first in (("settle", best), ("bot", real)):
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
    rows = [json.loads(line) for line in open(SET)]
    rng = random.Random(5)
    rng.shuffle(rows)
    rows = rows[:limit]
    out = SET.replace(".jsonl", f"_forced_{os.path.basename(net)}_{sims}.jsonl")
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
    ]
    for t in sorted({r["real"] for r in rows}):
        splits.append((f"real={t}", lambda r, t=t: r["real"] == t))
    for label, keep in splits:
        sel = [r for r in rows if keep(r)]
        if not sel:
            continue
        # one group per capture; value = settle win share - bot win share per position
        by = {}
        ws, wb, nd = [], [], 0
        for r in sel:
            s = [x[0] for x in r["res"]["settle"] if x[0] is not None]
            b = [x[0] for x in r["res"]["bot"] if x[0] is not None]
            nd += sum(x[0] is None for a in r["res"].values() for x in a)
            if not s or not b:
                continue
            ws.append(np.mean(s))
            wb.append(np.mean(b))
            by.setdefault(r["capture"], []).append((np.mean(s) - np.mean(b), 0))
        m, lo, hi = boot(list(by.values()), 0)
        better = sum(a > b for a, b in zip(ws, wb))
        worse = sum(a < b for a, b in zip(ws, wb))
        print(
            f"  {label}: {len(ws)} positions; win settle {100 * np.mean(ws):.1f}% vs bot move"
            f" {100 * np.mean(wb):.1f}%; diff {100 * m:+.1f} pts [{100 * lo:+.1f}, {100 * hi:+.1f}];"
            f" settle better in {better}, worse in {worse}; unfinished {nd}"
        )


def main():
    if sys.argv[1] == "sum":
        summary(sys.argv[2])
        return 0
    net, sims, games = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    limit = int(sys.argv[5]) if len(sys.argv) > 5 else 200
    workers = int(sys.argv[6]) if len(sys.argv) > 6 else 3
    run(net, sims, games, limit, workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
