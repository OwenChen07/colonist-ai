"""Robber failure set (E75 follow-up). Bot robber moves in 2100+ ranked games
(e75_robber.py's games) where the bot put the robber on its own pips although
a legal hex blocked the opponent at least as much and none of the bot's pips
(robber_bench.py's "needless self-block"). Up to PER_GAME per game, spread
over games, sampled to SIZE positions with a fixed seed.

  good = legal hexes with none of the bot's pips and the best net score
         (opponent pips minus own pips, cities double) among all legal hexes
  bad  = legal hexes with own pips where another legal hex has 0 own pips and
         at least as many opponent pips

    build [WORKERS]            -> data/orchestrator/live-loss/robber_set.jsonl
    score NET SIMS [WORKERS]   SIMS 0 = the raw prior's argmax, else search
                               (no noise). Prints the share of positions where
                               the choice is good / bad, game-clustered 95% CI.
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
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import jobs, col  # noqa: E402
from e75_aim import per_hex, scores  # noqa: E402

OUT = "data/orchestrator/live-loss/robber_set.jsonl"
SIZE, PER_GAME = 100, 2


def classify(game, me, opp):
    coords = {
        a.value[0]
        for a in game.playable_actions
        if a.action_type == ActionType.MOVE_ROBBER
    }
    hexes, own = per_hex(game.state.board)
    sc = scores(hexes, own, me, opp, [c for c in coords if c in hexes])
    for c in coords - set(sc):  # the desert: blocks nothing
        sc[c] = {"net": 0, "o": 0, "m": 0}
    best = max(v["net"] for v in sc.values())
    good = {c for c, v in sc.items() if v["m"] == 0 and v["net"] == best and v["o"] > 0}
    bad = {
        c
        for c, v in sc.items()
        if v["m"] > 0
        and any(w["m"] == 0 and w["o"] >= max(v["o"], 1) for w in sc.values())
    }
    return sc, good, bad


class Obs:
    """Collects the bot's robber decisions; with `target`, stops at the
    target-th one and keeps a copy of the game there."""

    def __init__(self, you, them, target=None):
        self.you, self.them, self.target = you, them, target
        self.k = -1
        self.rows, self.game = [], None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if (
            decision.action_type != ActionType.MOVE_ROBBER
            or col(decision.color) != self.you
        ):
            return
        if game.state.is_initial_build_phase:
            return
        self.k += 1
        if self.target is not None:
            if self.k == self.target:
                self.game = game.copy()
            return
        sc, good, bad = classify(game, self.you, self.them)
        chosen = decision.value[0]
        if chosen in bad and good:
            self.rows.append(
                {
                    "k": self.k,
                    "turn": game.state.num_turns,
                    "chosen": list(chosen),
                    "chosen_own": sc[chosen]["m"],
                    "chosen_opp": sc[chosen]["o"],
                    "best_net": max(v["net"] for v in sc.values()),
                    "good": sorted(list(c) for c in good),
                    "bad": sorted(list(c) for c in bad),
                }
            )


def players(path):
    record = convert(load_capture(path)).record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None, None, None
    you = you[0]
    return record, you, next(c for c in record["seats"] if c != you)


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
    rng = random.Random(path)
    rows = rng.sample(obs.rows, min(PER_GAME, len(obs.rows)))
    return [dict(r, capture=path, won=won, rolls=rolls) for r in rows]


def build(workers):
    todo = jobs()
    print(f"{len(todo)} games", flush=True)
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        rows = [r for part in pool.map(find, todo, chunksize=8) for r in part]
    games = {r["capture"] for r in rows}
    print(
        f"{len(rows)} candidate positions (<= {PER_GAME} a game) in {len(games)} games"
    )
    # one per game first, then a second from games picked at random
    rng = random.Random(75)
    by_game = {}
    for r in rows:
        by_game.setdefault(r["capture"], []).append(r)
    order = sorted(by_game)
    rng.shuffle(order)
    picked = [by_game[g][0] for g in order[:SIZE]]
    seconds = [by_game[g][1] for g in order if len(by_game[g]) > 1]
    picked += seconds[: max(0, SIZE - len(picked))]
    picked.sort(key=lambda r: (r["capture"], r["k"]))
    with open(OUT, "w") as f:
        for r in picked:
            f.write(json.dumps(r) + "\n")
    n_g = len({r["capture"] for r in picked})
    lost = sum(not r["won"] for r in picked)
    print(
        f"wrote {len(picked)} positions from {n_g} games to {OUT} ({lost} from lost games)"
    )
    print(
        f"  own pips blocked by the live move: mean {np.mean([r['chosen_own'] for r in picked]):.2f}"
    )
    print(f"  turn: median {int(np.median([r['turn'] for r in picked]))}")


_net = None


def score_one(args):
    global _net
    capture, rows, net_path, sims = args
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.net import Net

    if _net is None:
        _net = Net.load(net_path)
    out = []
    for i, r in enumerate(rows):
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
        qg = qb = None
        if sims == 0:
            legal = E.legal_indices(acts)
            static, dynamic = E.encode(game, game.state.current_color())
            logits, _ = _net.forward(static, dynamic[None, :])
            action = acts[int(np.argmax(logits[0, legal]))]
        else:
            seed = 7717 + 1009 * r["k"] + zlib.crc32(capture.encode()) % 1000
            search = Search(
                _net, simulations=sims, rng=random.Random(seed), noise=False
            )
            action, root = search.run(game)
            # the search's best visited Q among good hexes and among bad ones
            q = {}
            for a, n, w in zip(root.actions, root.N, root.W):
                if n > 0 and a.action_type == ActionType.MOVE_ROBBER:
                    c = list(a.value[0])
                    kind = "g" if c in r["good"] else "b" if c in r["bad"] else None
                    if kind:
                        q[kind] = max(q.get(kind, -9.0), w / n)
            qg, qb = q.get("g"), q.get("b")
        c = list(action.value[0])
        out.append((c in r["good"], c in r["bad"], qg, qb))
    return capture, out


def boot(groups, k, n=2000):
    rng = random.Random(1)

    def mean(gs):
        return sum(x[k] for g in gs for x in g) / max(1, sum(len(g) for g in gs))

    bs = sorted(mean(rng.choices(groups, k=len(groups))) for _ in range(n))
    return mean(groups), bs[int(0.025 * n)], bs[int(0.975 * n)]


def score(net_path, sims, workers):
    by_game = {}
    for line in open(OUT):
        r = json.loads(line)
        by_game.setdefault(r["capture"], []).append(r)
    todo = [(g, rows, net_path, sims) for g, rows in sorted(by_game.items())]
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        res = pool.map(score_one, todo, chunksize=1)
    groups = [o for _, o in res if o]
    n = sum(map(len, groups))
    how = "raw prior" if sims == 0 else f"search {sims} sims"
    print(f"{net_path} ({how}): {n} positions in {len(groups)} games")
    for k, name in (
        (0, "good (clean hex, best net)"),
        (1, "bad (needless self-block)"),
    ):
        m, lo, hi = boot(groups, k)
        print(f"  {name:28} {100 * m:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]")
    both = [(g, b) for o in groups for *_, g, b in o if g is not None and b is not None]
    if both:
        d = sorted(b - g for g, b in both)
        print(
            f"  search Q, best bad hex minus best good hex ({len(both)} positions with both visited):"
            f" median {np.median(d):+.3f}, bad ahead by >0.05 in {sum(x > 0.05 for x in d)},"
            f" good ahead by >0.05 in {sum(x < -0.05 for x in d)}"
        )


def main():
    if sys.argv[1] == "build":
        build(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        score(
            sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
