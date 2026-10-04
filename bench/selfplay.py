"""Phase 2 benchmark: one full self-play game with the real search and network.

    python bench/selfplay.py                       # untrained net, 50 simulations
    python bench/selfplay.py models/ab2/net.npz --simulations 50 --games 3

PLAN.md's rule: if a game takes more than 5 seconds per performance core,
speed up the slowest part before Phase 3. The breakdown shows which part that
is: copying positions and applying moves, building the inputs, the network,
or the search's own bookkeeping (the rest). Run it with nothing else busy, and
with one BLAS thread (set here), as self-play workers would.
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

import argparse  # noqa: E402
import random  # noqa: E402
import time  # noqa: E402

from catanatron.colonist import encode as E  # noqa: E402
from catanatron.colonist import mcts  # noqa: E402
from catanatron.colonist.net import Net, random_net  # noqa: E402
from catanatron.colonist.rules import new_game  # noqa: E402
from catanatron.game import Game  # noqa: E402
from catanatron.models.player import Color  # noqa: E402


class Timer:
    def __init__(self):
        self.total = {}

    def wrap(self, owner, name, label):
        original = getattr(owner, name)
        total = self.total
        total[label] = 0.0

        def timed(*args, **kwargs):
            t = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                total[label] += time.perf_counter() - t

        setattr(owner, name, timed)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", nargs="?", help="net.npz (default: untrained)")
    parser.add_argument("--simulations", type=int, default=50)
    parser.add_argument("--games", type=int, default=1)
    args = parser.parse_args()
    net = Net.load(args.net) if args.net else random_net()

    timer = Timer()
    timer.wrap(Game, "copy", "copy")
    timer.wrap(Game, "execute", "apply moves")
    timer.wrap(E, "encode", "inputs")
    timer.wrap(net, "forward", "network")

    searches = simulations = evaluations = 0
    start = time.perf_counter()
    for seed in range(args.games):
        random.seed(seed)
        players = [
            mcts.NetPlayer(
                c,
                mcts.NetPlayer.Params(simulations=args.simulations, seed=seed),
                net=net,
            )
            for c in (Color.RED, Color.BLUE)
        ]
        game = new_game(players, seed=seed)
        while game.winning_color() is None and game.state.num_turns < mcts.TURNS_LIMIT:
            actions = game.playable_actions
            if len(actions) == 1:
                game.execute(actions[0])
                continue
            search = mcts.Search(net, args.simulations, rng=random.Random(seed))
            action, root = search.run(game)
            searches += 1
            simulations += int(root.N.sum())
            evaluations += search.evaluations
            key = E.action_key(action)
            game.execute(next(a for a in actions if E.action_key(a) == key))
    elapsed = time.perf_counter() - start

    per_game = elapsed / args.games
    print(
        f"{per_game:.1f} s per game ({searches / args.games:.0f} searches, "
        f"{simulations / args.games:.0f} simulations, {evaluations / args.games:.0f} "
        f"network calls per game); {elapsed / simulations * 1e6:.0f} us per simulation"
    )
    accounted = 0.0
    for label, secs in timer.total.items():
        accounted += secs
        print(
            f"  {label:12s} {secs / args.games:6.2f} s per game  {secs / elapsed:5.1%}"
        )
    print(
        f"  {'search, rest':12s} {(elapsed - accounted) / args.games:6.2f} s per game  "
        f"{(elapsed - accounted) / elapsed:5.1%}"
    )
    print("PLAN.md threshold: 5 s per game per performance core")


if __name__ == "__main__":
    main()
