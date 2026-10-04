"""Self-play with the network and search (PLAN.md, Phase 3; benchmarked in Phase 2).

    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000
    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000  # resumes

Each worker process plays a shard of games all at once: every game's search is
a generator that pauses whenever it needs the network, and the pending
positions of all the games go through the network in one call. One position
at a time, the network costs several times more per position (PLAN.md, Time
and compute), so this is what makes self-play affordable.

Every searched position is recorded, with the search's improved policy over
the moves (`pi`) and its value (`v`) as training targets, and the game result
(`z`) once the game ends. Shards are written and resumed as in datagen.py, in
the same format plus `pi` (float16, over the 313 moves) and `v`.
"""

import argparse
import multiprocessing
import os
import random
import signal
import sys
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.datagen import shard_path
from catanatron.colonist.mcts import TURNS_LIMIT, Search


class _Game:
    """One game in progress and its records."""

    def __init__(self, seed, search_args):
        from catanatron.colonist.rules import new_game
        from catanatron.models.player import Color, Player

        self.seed = seed
        players = [Player(Color.RED), Player(Color.BLUE)]  # moves come from here
        self.game = new_game(players, seed=seed)
        self.game.random.seed(seed * 2 + 1)
        self.rng = random.Random(seed)
        self.search_args = search_args
        self.rows = []  # (dynamic, legal mask, pi, v, color, turn)
        self.search = None
        self.steps = None
        self.searches = 0

    def done(self):
        g = self.game
        return g.winning_color() is not None or g.state.num_turns >= TURNS_LIMIT

    def advance(self, net):
        """Plays forced moves, then starts or resumes the search. Returns the
        positions this game needs evaluated, or None when the game is over."""
        while True:
            if self.steps is None:
                while not self.done() and len(self.game.playable_actions) == 1:
                    self.game.execute(self.game.playable_actions[0])
                if self.done():
                    return None
                self.search = Search(net, rng=self.rng, **self.search_args)
                self.steps = self.search.steps(self.game)
                return next(self.steps)
            try:
                return self.steps.send(None)
            except StopIteration as finished:
                action, root = finished.value
                self.record(root)
                key = E.action_key(action)
                move = next(
                    a for a in self.game.playable_actions if E.action_key(a) == key
                )
                self.game.execute(move)
                self.steps = None
                self.searches += 1

    def record(self, root):
        pi, v = self.search.root_targets(root)
        legal = E.legal_indices(root.actions)
        _, dynamic = E.encode(self.game, root.color)
        mask = np.zeros(E.NUM_ACTIONS, bool)
        mask[legal] = True
        full_pi = np.zeros(E.NUM_ACTIONS, np.float32)
        full_pi[legal] = pi
        self.rows.append(
            (dynamic, mask, full_pi, v, root.color, self.game.state.num_turns)
        )


def play_games(net, seeds, search_args):
    """Plays the games together. Returns the finished _Games and the number
    of network calls."""
    games = [_Game(s, search_args) for s in seeds]
    pending = {}
    for g in games:
        nodes = g.advance(net)
        if nodes is not None:
            pending[g] = nodes
    calls = 0
    while pending:
        batch = [(g, n) for g, nodes in pending.items() for n in nodes]
        encoded = [E.encode(n.game, n.color) for _, n in batch]
        logits, values = net.forward_rows(
            [s for s, _ in encoded], np.stack([d for _, d in encoded])
        )
        calls += 1
        for row, (_, node) in enumerate(batch):
            legal = E.legal_indices(node.actions)
            node.set_evaluation(
                logits[row, legal].astype(np.float64), float(values[row])
            )
        for g in list(pending):
            nodes = g.advance(net)
            if nodes is None:
                del pending[g]
            else:
                pending[g] = nodes
    return games, calls


def run_shard(task):
    out_dir, net_path, first, count, search_args = task
    from catanatron.colonist.net import Net

    os.makedirs(out_dir, exist_ok=True)
    path = shard_path(out_dir, first, count)
    net = Net.load(net_path)
    start = time.time()
    games, calls = play_games(net, range(first, first + count), search_args)
    statics, seeds, cols = [], [], {k: [] for k in "dmpvzt"}
    gidx = []
    for i, g in enumerate(games):
        statics.append(E.encode_static(g.game.state.board.map))
        seeds.append(g.seed)
        winner = g.game.winning_color()
        for d, m, p, v, color, turn in g.rows:
            for k, x in zip("dmpvt", (d, m, p, v, turn)):
                cols[k].append(x)
            cols["z"].append(0 if winner is None else (1 if color == winner else -1))
            gidx.append(i)
    tmp = path + ".tmp.npz"
    np.savez_compressed(
        tmp,
        static=np.stack(statics).astype(np.float32),
        seed=np.asarray(seeds, np.int64),
        dynamic=np.stack(cols["d"]).astype(np.float16),
        game=np.asarray(gidx, np.int32),
        legal=np.packbits(np.stack(cols["m"]), axis=1),
        action=np.full(len(gidx), -1, np.int16),
        pi=np.stack(cols["p"]).astype(np.float16),
        v=np.asarray(cols["v"], np.float16),
        z=np.asarray(cols["z"], np.int8),
        turn=np.asarray(cols["t"], np.int16),
    )
    os.replace(tmp, path)
    searches = sum(g.searches for g in games)
    return path, count, len(gidx), searches, calls, time.time() - start


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net")
    parser.add_argument("out_dir")
    parser.add_argument("--games", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=2_000_000, help="first game seed")
    parser.add_argument("--simulations", type=int, default=50)
    parser.add_argument(
        "--shard-size",
        type=int,
        default=64,
        help="games per shard, all played at once in one worker",
    )
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    search_args = {"simulations": args.simulations}
    tasks = []
    for first in range(args.seed, args.seed + args.games, args.shard_size):
        count = min(args.shard_size, args.seed + args.games - first)
        if not os.path.exists(shard_path(args.out_dir, first, count)):
            tasks.append((args.out_dir, args.net, first, count, search_args))
    print(f"{len(tasks)} shards to play", flush=True)
    if not tasks:
        return 0
    start, games = time.time(), 0
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(args.workers) as pool:
        for path, count, rows, searches, calls, secs in pool.imap_unordered(
            run_shard, tasks
        ):
            games += count
            rate = games / (time.time() - start)
            print(
                f"{os.path.basename(path)}: {count} games in {secs:.0f} s "
                f"({secs / count:.1f} s per game in this worker; {searches / count:.0f} "
                f"searches per game, {searches * args.simulations / calls:.0f} "
                f"simulations per network call) | {games} games, {rate:.2f}/s",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
