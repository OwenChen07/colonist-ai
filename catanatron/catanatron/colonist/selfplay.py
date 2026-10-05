"""Self-play with the network and search (PLAN.md, Phase 3; benchmarked in Phase 2).

    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000
    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000  # resumes

Each worker process plays a shard of games all at once: every game's search is
a generator that pauses whenever it needs the network, and the pending
positions of all the games go through the network in one call. One position
at a time, the network costs several times more per position (PLAN.md, Time
and compute), so this is what makes self-play affordable.

A share of games (--opponent-rate) put a random opponent in one seat: an
older net or a reference bot, so the net doesn't only learn to beat itself.
Only the main net's moves are recorded from those games.

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


def load_opponent(spec):
    """An opponent: a net file (searched like the main net) or a registry
    bot spec such as AB:2 (asked for its move directly)."""
    if spec.endswith(".npz"):
        from catanatron.colonist.net import Net

        return Net.load(spec)
    return spec


class _Game:
    """One game in progress and its records. `seats` maps each color to what
    plays it: the main Net, another Net, or a bot spec string."""

    def __init__(self, seed, search_args, seats, main):
        from catanatron.colonist.rules import new_game
        from catanatron.models.player import Color, Player

        self.seed = seed
        players = [Player(Color.RED), Player(Color.BLUE)]  # moves come from here
        self.game = new_game(players, seed=seed)
        self.game.random.seed(seed * 2 + 1)
        self.rng = random.Random(seed)
        self.search_args = search_args
        self.main = main
        self.seats = {}
        for color, agent in seats.items():
            if isinstance(agent, str):
                import catanatron.players  # noqa: F401
                from catanatron.registry import REGISTRY

                agent = REGISTRY.build(agent, color)
            self.seats[color] = agent
        self.rows = []  # (dynamic, legal mask, pi, v, color, turn)
        self.search = None
        self.search_net = None
        self.steps = None
        self.searches = 0

    def done(self):
        g = self.game
        return g.winning_color() is not None or g.state.num_turns >= TURNS_LIMIT

    def advance(self):
        """Plays forced moves and bot moves, then starts or resumes a search.
        Returns (net, positions to evaluate), or None when the game is over."""
        while True:
            if self.steps is None:
                while not self.done() and len(self.game.playable_actions) == 1:
                    self.game.execute(self.game.playable_actions[0])
                if self.done():
                    return None
                agent = self.seats[self.game.state.current_color()]
                if not hasattr(agent, "forward"):  # a bot
                    self.game.execute(
                        agent.decide(self.game, self.game.playable_actions)
                    )
                    continue
                random.seed(self.rng.random())  # for bots that use the global stream
                self.search_net = agent
                self.search = Search(agent, rng=self.rng, **self.search_args)
                self.steps = self.search.steps(self.game)
                return agent, next(self.steps)
            try:
                return self.search_net, self.steps.send(None)
            except StopIteration as finished:
                action, root = finished.value
                if self.search_net is self.main:  # train only on the main net's moves
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


def seat_plan(seed, net, opponents, opponent_rate):
    """Who plays each color in game `seed`: the main net in both seats, or,
    at `opponent_rate`, one seat to a random opponent. Fixed by the seed."""
    from catanatron.models.player import Color

    rng = random.Random(seed ^ 0x0FF0)
    if opponents and rng.random() < opponent_rate:
        other = opponents[rng.randrange(len(opponents))]
        if rng.random() < 0.5:
            return {Color.RED: net, Color.BLUE: other}
        return {Color.RED: other, Color.BLUE: net}
    return {Color.RED: net, Color.BLUE: net}


def play_games(net, seeds, search_args, opponents=(), opponent_rate=0.0):
    """Plays the games together, one network call per net per step. Returns
    the finished _Games and the number of network calls."""
    games = [
        _Game(s, search_args, seat_plan(s, net, list(opponents), opponent_rate), net)
        for s in seeds
    ]
    pending = {}
    for g in games:
        step = g.advance()
        if step is not None:
            pending[g] = step
    calls = 0
    while pending:
        by_net = {}
        for g, (n, nodes) in pending.items():
            by_net.setdefault(id(n), (n, []))[1].extend(nodes)
        for n, nodes in by_net.values():
            encoded = [E.encode(node.game, node.color) for node in nodes]
            logits, values = n.forward_rows(
                [st for st, _ in encoded], np.stack([d for _, d in encoded])
            )
            calls += 1
            for row, node in enumerate(nodes):
                legal = E.legal_indices(node.actions)
                node.set_evaluation(
                    logits[row, legal].astype(np.float64), float(values[row])
                )
        for g in list(pending):
            step = g.advance()
            if step is None:
                del pending[g]
            else:
                pending[g] = step
    return games, calls


def run_shard(task):
    out_dir, net_path, first, count, search_args, *rest = task
    opponent_specs, opponent_rate = rest if rest else ((), 0.0)
    from catanatron.colonist.net import Net

    os.makedirs(out_dir, exist_ok=True)
    path = shard_path(out_dir, first, count)
    net = Net.load(net_path)
    opponents = [load_opponent(o) for o in opponent_specs]
    start = time.time()
    games, calls = play_games(
        net, range(first, first + count), search_args, opponents, opponent_rate
    )
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


def run(
    net_path,
    out_dir,
    games=5000,
    seed=2_000_000,
    simulations=50,
    shard_size=64,
    workers=None,
    opponents=(),
    opponent_rate=0.0,
    log=print,
):
    """Plays the shards of out_dir that aren't on disk yet."""
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")
    search_args = {"simulations": simulations}
    tasks = []
    for first in range(seed, seed + games, shard_size):
        count = min(shard_size, seed + games - first)
        if not os.path.exists(shard_path(out_dir, first, count)):
            tasks.append(
                (
                    out_dir,
                    net_path,
                    first,
                    count,
                    search_args,
                    tuple(opponents),
                    opponent_rate,
                )
            )
    log(f"{len(tasks)} shards to play")
    if not tasks:
        return
    start, played = time.time(), 0
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        for path, count, rows, searches, calls, secs in pool.imap_unordered(
            run_shard, tasks
        ):
            played += count
            rate = played / (time.time() - start)
            log(
                f"{os.path.basename(path)}: {count} games in {secs:.0f} s "
                f"({secs / count:.1f} s per game in this worker; {searches / count:.0f} "
                f"searches per game, {searches * simulations / max(calls, 1):.0f} "
                f"simulations per network call) | {played} games, {rate:.2f}/s"
            )


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
    parser.add_argument(
        "--opponents",
        default="",
        help="comma-separated nets (.npz) or bot specs (e.g. AB:2) for some games",
    )
    parser.add_argument(
        "--opponent-rate",
        type=float,
        default=0.2,
        help="share of games where one seat is a random opponent",
    )
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    opponents = [o for o in args.opponents.split(",") if o]
    run(
        args.net,
        args.out_dir,
        args.games,
        args.seed,
        args.simulations,
        args.shard_size,
        args.workers,
        opponents,
        args.opponent_rate,
        log=lambda m: print(m, flush=True),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
