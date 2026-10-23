"""Skipped-settlement positions (room2.py follow-up). Bot turns in 2100+ ranked
games (e75_robber.py's games) where the bot could pay for a settlement, had an
open spot of MIN_PIPS+ pips on its roads, and built no settlement that turn.
The position is the turn's first bot decision with BUILD_SETTLEMENT legal.
Up to PER_GAME a game, SIZE from lost games and SIZE from won games.

    build [WORKERS]            -> data/orchestrator/live-loss/settle_set.jsonl
    score NET SIMS [WORKERS]   SIMS 0 = the raw prior (mass on settle, argmax),
                               else search (no noise): share choosing settle,
                               and the root's best settle Q minus best other Q.
"""

import json
import os
import random
import sys
import zlib
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist import encode as E  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from e75_robber import jobs, col  # noqa: E402
from robber_set import players, boot  # noqa: E402
from room2 import node_pips  # noqa: E402
from catanatron.state_functions import (  # noqa: E402
    get_actual_victory_points,
    get_visible_victory_points,
)

OUT = "data/orchestrator/live-loss/settle_set.jsonl"
SIZE, PER_GAME, MIN_PIPS = 100, 2, 5
S = ActionType.BUILD_SETTLEMENT


class Obs:
    """Indexes the bot's decisions with BUILD_SETTLEMENT legal (k); with
    `target`, keeps a copy of the game at the target-th one."""

    def __init__(self, you, them, target=None):
        self.you, self.them, self.target = you, them, target
        self.k = -1
        self.turns = {}  # num_turns -> first candidate + whether it settled
        self.game = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase or col(d.color) != self.you:
            return
        row = self.turns.get(s.num_turns)
        if row is not None and d.action_type == S:
            row["settled"] = True
        if not any(a.action_type == S for a in game.playable_actions):
            return
        self.k += 1
        if self.target is not None:
            if self.k == self.target:
                self.game = game.copy()
            return
        if row is None:
            spot = max(
                node_pips(s.board, a.value)
                for a in game.playable_actions
                if a.action_type == S
            )
            self.turns[s.num_turns] = {
                "k": self.k,
                "turn": s.num_turns,
                "spot": spot,
                "first": d.action_type.value,
                "settled": d.action_type == S,
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
    cand = [r for r in obs.turns.values() if not r["settled"] and r["spot"] >= MIN_PIPS]
    rng = random.Random(path)
    rows = rng.sample(cand, min(PER_GAME, len(cand)))
    return [dict(r, capture=path, won=won, rolls=rolls) for r in rows]


def build(workers):
    todo = jobs()
    print(f"{len(todo)} games", flush=True)
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        rows = [r for part in pool.map(find, todo, chunksize=8) for r in part]
    picked = []
    for won in (False, True):
        by_game = {}
        for r in rows:
            if r["won"] == won:
                by_game.setdefault(r["capture"], []).append(r)
        print(
            f"won={won}: {sum(map(len, by_game.values()))} candidates in {len(by_game)} games"
        )
        rng = random.Random(77)
        order = sorted(by_game)
        rng.shuffle(order)
        part = [by_game[g][0] for g in order[:SIZE]]
        part += [by_game[g][1] for g in order if len(by_game[g]) > 1][
            : max(0, SIZE - len(part))
        ]
        picked += part
    picked.sort(key=lambda r: (r["capture"], r["k"]))
    with open(OUT, "w") as f:
        for r in picked:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(picked)} positions to {OUT}")
    for won in (False, True):
        p = [r for r in picked if r["won"] == won]
        print(
            f"  won={won}: {len(p)}, spot pips mean {np.mean([r['spot'] for r in p]):.1f},"
            f" turn median {int(np.median([r['turn'] for r in p]))},"
            f" behind on VP {np.mean([r['vp'] < r['ovp'] for r in p]):.0%}"
        )


_nets = {}


def score_one(args):
    capture, rows, net_path, sims = args
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.net import Net

    if net_path not in _nets:
        _nets[net_path] = Net.load(net_path)
    net = _nets[net_path]
    out = []
    for r in rows:
        try:
            record, you, them = players(capture)
            obs = Obs(you, them, target=r["k"])
            replay(record, observer=obs)
        except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
            continue
        game = obs.game
        if game is None:
            continue
        acts = game.playable_actions
        is_s = np.array([a.action_type == S for a in acts])
        legal = E.legal_indices(acts)
        static, dynamic = E.encode(game, game.state.current_color())
        logits, _ = net.forward(static, dynamic[None, :])
        lg = logits[0, legal]
        p = np.exp(lg - lg.max())
        p /= p.sum()
        mass = float(p[is_s].sum())
        dq = None
        if sims == 0:
            pick = bool(is_s[int(np.argmax(lg))])
        else:
            seed = 7717 + 1009 * r["k"] + zlib.crc32(capture.encode()) % 1000
            search = Search(net, simulations=sims, rng=random.Random(seed), noise=False)
            action, root = search.run(game)
            pick = action.action_type == S
            q = {}
            for a, n, w in zip(root.actions, root.N, root.W):
                if n > 0:
                    k = a.action_type == S
                    q[k] = max(q.get(k, -9.0), w / n)
            if True in q and False in q:
                dq = q[True] - q[False]
        out.append({"won": r["won"], "pick": pick, "mass": mass, "dq": dq, "k": r["k"]})
    return capture, out


def score(net_path, sims, workers):
    by_game = {}
    for line in open(OUT):
        r = json.loads(line)
        by_game.setdefault(r["capture"], []).append(r)
    todo = [(g, rows, net_path, sims) for g, rows in sorted(by_game.items())]
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        res = pool.map(score_one, todo, chunksize=1)
    how = "raw prior" if sims == 0 else f"search {sims} sims"
    print(f"{net_path} ({how})")
    for won in (False, True):
        groups = [
            [(x["pick"], x["mass"]) for x in o if x["won"] == won] for _, o in res
        ]
        groups = [g for g in groups if g]
        n = sum(map(len, groups))
        m, lo, hi = boot(groups, 0)
        mm, mlo, mhi = boot(groups, 1)
        print(
            f"  {'won' if won else 'lost'} games, {n} positions in {len(groups)} games:"
            f" picks settle {100 * m:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}],"
            f" prior mass on settle {100 * mm:.1f}% [{100 * mlo:.1f}, {100 * mhi:.1f}]"
        )
        d = sorted(
            x["dq"]
            for _, o in res
            for x in o
            if x["won"] == won and x["dq"] is not None
        )
        if d:
            print(
                f"    Q(best settle) - Q(best other), {len(d)} with both visited: median {np.median(d):+.3f},"
                f" settle ahead >0.05 in {sum(x > 0.05 for x in d)}, behind <-0.05 in {sum(x < -0.05 for x in d)}"
            )
    with open(
        OUT.replace(".jsonl", f"_score_{os.path.basename(net_path)}_{sims}.json"), "w"
    ) as f:
        json.dump(res, f)


def main():
    if sys.argv[1] == "debug":
        path = jobs()[0][0]
        record, you, them = players(path)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        print(rep.errors[:3], len(obs.turns), list(obs.turns.values())[:3])
        return 0
    if sys.argv[1] == "build":
        build(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        score(
            sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
