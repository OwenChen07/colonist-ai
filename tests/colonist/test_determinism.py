"""Seeded games must be identical from one process to the next.

Python randomizes string hashing per process, and before 3.12 hashed None
by its address, so anything that iterates a set of moves can come out in a
different order each run and change a bot's tie-breaks. The evaluation
harness depends on seeded games being repeatable.
"""

import os
import subprocess
import sys

SCRIPT = """
import random
from catanatron import Color
from catanatron.colonist.rules import new_game
from catanatron.players import AlphaBetaPlayer, ValueFunctionPlayer
for seed in range(3):
    random.seed(seed)
    g = new_game([AlphaBetaPlayer(Color.RED), ValueFunctionPlayer(Color.BLUE)], seed=seed)
    g.play()
    for r in g.state.action_records:
        print(r.action.color.value, r.action.action_type.value, r.action.value, r.result)
"""


def run(hash_seed):
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def test_seeded_games_repeat_across_processes():
    first = run(1)
    assert first
    assert run(2) == first
