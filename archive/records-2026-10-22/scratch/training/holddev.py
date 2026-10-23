"""Calibrate the human seat's dev_play_bias (E13 follow-up): for each bias,
GAMES games of NET vs the human seat (rate 1, plain search at SIMS), and the
human seat's held action cards at turn TURN+ (people: 1.37 [1.29, 1.46]).
Writes the biased model files to data/orchestrator/training/.

usage: holddev.py GAMES SIMS TURN BIAS [BIAS ...]
"""

import multiprocessing as mp
import os
import random
import sys

import numpy as np

WT = "/home/owen/colonist-ai-wt/training"
os.chdir(WT)
sys.path.insert(0, f"{WT}/catanatron")
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "1")

NET = "runs/g1/nets/round_073.npz"
HUMAN = "models/human/net.npz"
OUTDIR = "data/orchestrator/training"
CHUNK = 4


def model_path(bias):
    return f"{OUTDIR}/human_holddev_{bias:+.1f}.npz"


def make_model(bias):
    os.makedirs(OUTDIR, exist_ok=True)
    z = dict(np.load(HUMAN, allow_pickle=False))
    z["meta_dev_play_bias"] = np.asarray(float(bias))
    np.savez(model_path(bias), **z)


def play(task):
    bias, first, count, turn_min, sims = task
    from catanatron.colonist import encode as E
    from catanatron.colonist import selfplay as S
    from catanatron.colonist.net import Net
    from catanatron.state_functions import player_key

    net = Net.load(NET)
    human = S.HumanModel(Net.load(model_path(bias)))
    assert human.dev_play_bias == bias
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
    return bias, rows, wins, turns


def ci(groups, reps=1000):
    rng = random.Random(0)
    keys = list(groups)
    mean = lambda ks: sum(sum(groups[k]) for k in ks) / max(
        1, sum(len(groups[k]) for k in ks)
    )
    boots = sorted(mean([rng.choice(keys) for _ in keys]) for _ in range(reps))
    return f"{mean(keys):.2f} [{boots[25]:.2f}, {boots[974]:.2f}]"


def main():
    sys.stdout = sys.stderr = open("data/orchestrator/jobs/holddev.log", "w", 1)
    with open("data/orchestrator/jobs/holddev.pid", "w") as stream:
        stream.write(f"{os.getpid()}\n")
    n, sims, turn_min = map(int, sys.argv[1:4])
    biases = [float(b) for b in sys.argv[4:]]
    for b in biases:
        make_model(b)
    tasks = [
        (b, 7_300_000_000 + f, min(CHUNK, n - f), turn_min, sims)
        for b in biases
        for f in range(0, n, CHUNK)
    ]
    out = {b: ({}, [0], []) for b in biases}
    with mp.Pool(3) as pool:
        for i, (b, rows, wins, turns) in enumerate(pool.imap_unordered(play, tasks)):
            groups, w, t = out[b]
            for sd, held in rows:
                groups.setdefault(sd, []).append(held)
            w[0] += wins
            t.extend(turns)
            print(f"task {i + 1}/{len(tasks)}", flush=True)
            if len(t) == n:
                report(b, out[b], turn_min)


def report(b, result, turn_min):
    groups, w, t = result
    if True:
        print(
            f"bias {b:+.1f}: net wins {w[0]}/{len(t)}, median turns "
            f"{int(np.median(t))}, games reaching turn {turn_min} {len(groups)}, "
            f"human seat action cards held {ci(groups) if groups else '-'}",
            flush=True,
        )


if __name__ == "__main__":
    main()
