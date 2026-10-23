"""E59: how sigma's scale in the root target changes its noise and its reach.

    e59_scale.py NET GAMES SEED

e51_kl.py play's procedure (NET at 400 simulations, a quarter of the
non-setup decisions), but each sampled position gets a second independent
400 search, and both roots' targets are taken at scales 1, 0.5, 0.25, 0.1
(the same searches; only the target changes). Per scale: top-1 agreement and
KL between the two targets, and the first target's KL to the prior.
"""

import os
import random
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from e51_kl import kl, softmax  # noqa: E402

from catanatron.colonist import encode as E  # noqa: E402
from catanatron.colonist.mcts import Search  # noqa: E402
from catanatron.colonist.net import Net  # noqa: E402
from catanatron.colonist.rules import new_game  # noqa: E402
from catanatron.models.player import Color, RandomPlayer  # noqa: E402

SCALES = (1.0, 0.5, 0.25, 0.1)


def main(net_path, games, seed):
    net = Net.load(net_path)
    rng = random.Random(int(seed))
    rows = {s: [] for s in SCALES}  # (KL a||b, top1 agree, KL a||prior, entropy a)
    for g in range(int(games)):
        game = new_game(
            [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=int(seed) + g
        )
        while game.winning_color() is None and game.state.num_turns < 400:
            acts = game.playable_actions
            if len(acts) == 1:
                game.execute(acts[0])
                continue
            setup = game.state.is_initial_build_phase
            s = Search(net, 400, rng=rng)
            action, root = s.run(game)
            if not setup and root.immediate_win is None and rng.random() < 0.25:
                prior = softmax(np.asarray(root.logits, np.float64))
                sb = Search(net, 400, rng=rng)
                _, rb = sb.run(game)
                for sc in SCALES:
                    pa = s.root_targets(root, scale=sc)[0].astype(np.float64)
                    pb = sb.root_targets(rb, scale=sc)[0].astype(np.float64)
                    m = pa > 0
                    ent = float(-(pa[m] * np.log(pa[m])).sum())
                    rows[sc].append(
                        (
                            kl(pb, pa),
                            float(pa.argmax() == pb.argmax()),
                            kl(pa, prior),
                            ent,
                        )
                    )
            key = E.action_key(action)
            game.execute(next(a for a in acts if E.action_key(a) == key))
        print(
            f"game {g}: {game.state.num_turns} turns, {len(rows[1.0])} positions",
            flush=True,
        )
    print(f"RESULT {net_path} games {games} seed {seed}")
    for sc, r in rows.items():
        a = np.asarray(r)
        print(
            f"ROWS scale {sc}: n {len(a)}  KL(b||a) {a[:,0].mean():.4f} (median {np.median(a[:,0]):.4f})  "
            f"top1 agree {a[:,1].mean():.4f}  KL(a||prior) {a[:,2].mean():.4f} "
            f"(median {np.median(a[:,2]):.4f})  entropy {a[:,3].mean():.3f}"
        )
    out = "/home/owen/colonist-ai/data/orchestrator/e59"
    os.makedirs(out, exist_ok=True)
    np.save(f"{out}/rows-{seed}.npy", np.asarray([rows[s] for s in SCALES]))


if __name__ == "__main__":
    main(*sys.argv[1:])
