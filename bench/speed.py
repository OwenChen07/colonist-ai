"""Speed baseline on this machine, colonist 1v1 settings (15 VP, 9-card limit, friendly robber).

Run: .venv/bin/python bench/speed.py
"""

import random
import time

import numpy as np

from catanatron import Color, Game
from catanatron.players import AlphaBetaPlayer, RandomPlayer, ValueFunctionPlayer

RULES = dict(vps_to_win=15, discard_limit=9, friendly_robber=True)


def games_per_sec(make_players, n):
    t = time.perf_counter()
    turns = decisions = 0
    for seed in range(n):
        g = Game(make_players(), seed=seed, **RULES)
        g.play()
        turns += g.state.num_turns
        decisions += len(g.state.action_records)
    dt = time.perf_counter() - t
    return n / dt, turns / n, decisions / n


def step_time(n=3000):
    # Copy a mid-game position and apply one legal action: the inner step of tree search.
    g = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=1, **RULES)
    for _ in range(150):
        g.play_tick()
    rng = random.Random(0)
    t = time.perf_counter()
    for _ in range(n):
        c = g.copy()
        c.execute(rng.choice(c.playable_actions))
    return (time.perf_counter() - t) / n


def net_time(batch, n=2000):
    # ~1M-parameter MLP (650 -> 768 -> 768 -> 332 policy + 1 value), float32, numpy/Accelerate.
    rng = np.random.default_rng(0)
    ws = [
        rng.standard_normal(s, dtype=np.float32) * 0.05
        for s in [(650, 768), (768, 768), (768, 333)]
    ]
    x = rng.standard_normal((batch, 650), dtype=np.float32)
    t = time.perf_counter()
    for _ in range(n):
        h = x
        for w in ws[:-1]:
            h = np.maximum(h @ w, 0)
        h @ ws[-1]
    return (time.perf_counter() - t) / n / batch, sum(w.size for w in ws)


def feature_time(n=300):
    # Catanatron's feature extraction (create_sample_vector): what the network would read each simulation.
    from catanatron.features import create_sample_vector

    g = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=1, **RULES)
    for _ in range(150):
        g.play_tick()
    t = time.perf_counter()
    for _ in range(n):
        v = create_sample_vector(g, Color.RED)
    return (time.perf_counter() - t) / n, len(v)


if __name__ == "__main__":
    r = games_per_sec(lambda: [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], 100)
    print(
        f"Random vs Random: {r[0]:.1f} games/s, {r[1]:.0f} turns, {r[2]:.0f} decisions/game"
    )
    r = games_per_sec(
        lambda: [ValueFunctionPlayer(Color.RED), ValueFunctionPlayer(Color.BLUE)], 20
    )
    print(
        f"ValueFunction vs ValueFunction: {r[0]:.2f} games/s, {r[1]:.0f} turns, {r[2]:.0f} decisions/game"
    )
    r = games_per_sec(
        lambda: [AlphaBetaPlayer(Color.RED), ValueFunctionPlayer(Color.BLUE)], 5
    )
    print(f"AlphaBeta vs ValueFunction: {1 / r[0]:.1f} s/game")
    print(f"Search step (copy + one move): {step_time() * 1e6:.0f} us")
    for b in (1, 64):
        per, params = net_time(b)
        print(
            f"Net eval, {params / 1e6:.2f}M params, batch {b}: {per * 1e6:.0f} us/position"
        )
    per, size = feature_time()
    print(f"Feature extraction ({size} numbers): {per * 1e6:.0f} us/position")
