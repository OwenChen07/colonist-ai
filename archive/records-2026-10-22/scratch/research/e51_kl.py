"""E51: how far round 73's search moves its policy from the net's prior.

shards ROUND_DIR NET [MAX_SHARDS]
    Full-search rows of self-play shards: KL(pi || prior) in nats, top-1
    agreement, |v_search - v_net|, by phase.
play NET GAMES SEED
    Plays GAMES games with NET at 400 simulations; at a quarter of the
    non-setup decisions also runs a second 400 search (noise baseline) and
    an 800 search on the same position. Prints paired KLs.
"""

import glob
import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net


def softmax(x):
    x = x - x.max()
    p = np.exp(x)
    return p / p.sum()


def kl(p, q):
    p = np.asarray(p, np.float64)
    q = np.asarray(q, np.float64)
    m = p > 0
    return float((p[m] * np.log(p[m] / np.maximum(q[m], 1e-12))).sum())


def summary(name, rows):
    if not rows:
        return
    a = np.asarray(rows, np.float64)
    n = len(a)
    se = a[:, 0].std() / np.sqrt(n)
    print(
        f"{name:>10}: n {n:6d}  KL {a[:,0].mean():.3f} ± {1.96*se:.3f} "
        f"(median {np.median(a[:,0]):.3f})  top1 agree {a[:,1].mean():.1%}  "
        f"pi on prior's top {a[:,2].mean():.3f}  |dv| {a[:,3].mean():.3f}"
    )


def shards(round_dir, net_path, max_shards=20):
    net = Net.load(net_path)
    files = sorted(glob.glob(f"{round_dir}/data/shard_*.npz"))[: int(max_shards)]
    phases = {"setup": [], "early": [], "mid": [], "late": []}
    for f in files:
        d = np.load(f)
        full = d["full"]
        legal = np.unpackbits(d["legal"], axis=1)[:, : E.NUM_ACTIONS].astype(bool)
        idx = np.nonzero(full & (legal.sum(1) > 1))[0]
        statics = [d["static"][g] for g in d["game"][idx]]
        dyn = d["dynamic"][idx].astype(np.float32)
        for s in range(0, len(idx), 512):
            sl = slice(s, s + 512)
            logits, values = net.forward_rows(statics[sl], dyn[sl])
            for j, i in enumerate(idx[sl]):
                lg = np.nonzero(legal[i])[0]
                prior = softmax(logits[j, lg].astype(np.float64))
                pi = d["pi"][i, lg].astype(np.float64)
                pi /= pi.sum()
                top = prior.argmax()
                row = (
                    kl(pi, prior),
                    float(pi.argmax() == top),
                    pi[top],
                    abs(float(d["v"][i]) - float(values[j])),
                )
                t = int(d["turn"][i])
                ph = (
                    "setup"
                    if t == 0
                    else "early" if t < 30 else "mid" if t < 75 else "late"
                )
                phases[ph].append(row)
    print(f"{len(files)} shards of {round_dir}, net {net_path}")
    for k, v in phases.items():
        summary(k, v)
    summary("non-setup", sum((v for k, v in phases.items() if k != "setup"), []))


def play(net_path, games, seed):
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.rules import new_game
    from catanatron.models.player import Color, RandomPlayer

    net = Net.load(net_path)
    rng = random.Random(int(seed))
    rows = {"400": [], "400b": [], "800": [], "800v400": [], "400bv400": []}
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
                top = prior.argmax()
                pi4, v4 = s.root_targets(root)
                sb = Search(net, 400, rng=rng)
                _, rb = sb.run(game)
                pib, vb = sb.root_targets(rb)
                s8 = Search(net, 800, rng=rng)
                _, r8 = s8.run(game)
                pi8, v8 = s8.root_targets(r8)
                _, vnet = net.evaluate(game, root.color, E.legal_indices(root.actions))
                for name, pi, v in (
                    ("400", pi4, v4),
                    ("400b", pib, vb),
                    ("800", pi8, v8),
                ):
                    rows[name].append(
                        (
                            kl(pi, prior),
                            float(pi.argmax() == top),
                            pi[top],
                            abs(v - vnet),
                        )
                    )
                rows["800v400"].append(
                    (
                        kl(pi8, pi4),
                        float(pi8.argmax() == pi4.argmax()),
                        0.0,
                        abs(v8 - v4),
                    )
                )
                rows["400bv400"].append(
                    (
                        kl(pib, pi4),
                        float(pib.argmax() == pi4.argmax()),
                        0.0,
                        abs(vb - v4),
                    )
                )
            key = E.action_key(action)
            game.execute(next(a for a in acts if E.action_key(a) == key))
        print(
            f"game {g}: {game.state.num_turns} turns, {len(rows['400'])} positions",
            flush=True,
        )
        for k, v in rows.items():
            summary(k, v)


if __name__ == "__main__":
    {"shards": shards, "play": play}[sys.argv[1]](*sys.argv[2:])
