"""E55 check: action cards the human seat holds at turn TURN+ (people: 1.37
[1.29, 1.46]), for several people models at dev_play_bias 0, same seeds.
Adapted from scratch/training/holddev.py (NET vs the seat, plain search at SIMS).

usage: holddev_e55.py GAMES SIMS TURN name=model.npz [name=model.npz ...]
"""

import multiprocessing as mp
import os
import random
import sys

import numpy as np

WT = "/home/owen/colonist-ai-wt/research"
os.chdir(WT)
sys.path.insert(0, f"{WT}/catanatron")
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "1")

NET = "runs/g1/nets/round_073.npz"
OUTDIR = "data/orchestrator/e55"
LOG = "data/orchestrator/jobs/holddev-e55"
CHUNK = 4


def model_path(name):
    return f"{OUTDIR}/holddev_{name}.npz"


def make_model(name, src):
    z = dict(np.load(src, allow_pickle=False))
    z["meta_dev_play_bias"] = np.asarray(0.0)
    np.savez(model_path(name), **z)


def play(task):
    name, first, count, turn_min, sims = task
    from catanatron.colonist import encode as E
    from catanatron.colonist import selfplay as S
    from catanatron.colonist.net import Net
    from catanatron.state_functions import player_key

    net = Net.load(NET)
    human = S.HumanModel(Net.load(model_path(name)))
    games = []
    for s in range(first, first + count):
        seats = S.seat_plan(s, net, [], 0.0, None, human, 1.0)
        games.append(S._Game(s, {"simulations": sims}, seats, net))
    rows, seen = [], set()

    def record(g):
        st = g.game.state
        if st.is_initial_build_phase or st.num_turns < turn_min:
            return
        if (g.seed, st.num_turns) in seen:
            return
        seen.add((g.seed, st.num_turns))
        (hc,) = [c for c, a in g.seats.items() if isinstance(a, S._HumanSeat)]
        p = player_key(st, hc)
        held = sum(
            st.player_state[f"{p}_{d}_IN_HAND"]
            for d in ("KNIGHT", "YEAR_OF_PLENTY", "MONOPOLY", "ROAD_BUILDING")
        )
        rows.append((g.seed, held))

    pending = {}
    for g in games:
        step = g.advance()
        record(g)
        if step is not None:
            pending[g] = step
    while pending:
        by_net = {}
        for g, (n, nodes) in pending.items():
            by_net.setdefault(id(n), (n, []))[1].extend(nodes)
        for n, nodes in by_net.values():
            enc = [E.encode(node.game, node.color) for node in nodes]
            logits, values = n.forward_rows(
                [st for st, _ in enc], np.stack([d for _, d in enc])
            )
            for r, node in enumerate(nodes):
                legal = E.legal_indices(node.actions)
                node.set_evaluation(
                    logits[r, legal].astype(np.float64), float(values[r])
                )
        for g in list(pending):
            step = g.advance()
            record(g)
            if step is None:
                del pending[g]
            else:
                pending[g] = step
    wins, turns = 0, []
    for g in games:
        (hc,) = [c for c, a in g.seats.items() if isinstance(a, S._HumanSeat)]
        wins += g.game.winning_color() not in (None, hc)
        turns.append(g.game.state.num_turns)
    return name, rows, wins, turns


def ci(groups, reps=1000):
    rng = random.Random(0)
    keys = list(groups)
    mean = lambda ks: sum(sum(groups[k]) for k in ks) / max(
        1, sum(len(groups[k]) for k in ks)
    )
    boots = sorted(mean([rng.choice(keys) for _ in keys]) for _ in range(reps))
    return f"{mean(keys):.2f} [{boots[25]:.2f}, {boots[974]:.2f}]"


def main():
    sys.stdout = sys.stderr = open(f"{LOG}.log", "w", 1)
    with open(f"{LOG}.pid", "w") as stream:
        stream.write(f"{os.getpid()}\n")
    n, sims, turn_min = map(int, sys.argv[1:4])
    models = dict(a.split("=", 1) for a in sys.argv[4:])
    for name, src in models.items():
        make_model(name, src)
    tasks = [
        (name, 7_300_000_000 + f, min(CHUNK, n - f), turn_min, sims)
        for f in range(0, n, CHUNK)
        for name in models
    ]
    out = {name: ({}, [0], []) for name in models}
    with mp.Pool(3) as pool:
        for i, (name, rows, wins, turns) in enumerate(pool.imap_unordered(play, tasks)):
            groups, w, t = out[name]
            for sd, held in rows:
                groups.setdefault(sd, []).append(held)
            w[0] += wins
            t.extend(turns)
            print(f"task {i + 1}/{len(tasks)}", flush=True)
            if len(t) == n:
                print(
                    f"{name}: net wins {w[0]}/{len(t)}, median turns "
                    f"{int(np.median(t))}, games reaching turn {turn_min} "
                    f"{len(groups)}, human seat action cards held "
                    f"{ci(groups) if groups else '-'}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
