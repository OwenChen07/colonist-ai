"""Old vs new Search.improved_policy (and the descent's pick) on node states
recorded from real searches; bit-identical check and time per call.

usage: q_bench.py [moves]
"""

import random
import sys
import timeit
import types

import numpy as np

from catanatron.colonist import mcts as M
from catanatron.colonist.net import Net
from catanatron.colonist.selfplay import fresh_game


def old_completed_q(self, node):
    visited = node.N > 0
    q = np.zeros(len(node.N))
    q[visited] = node.W[visited] / node.N[visited]
    total = node.N.sum()
    if total == 0:
        v_mix = node.value
    else:
        pv = node.prior[visited]
        v_mix = (node.value + total / pv.sum() * (pv * q[visited]).sum()) / (1 + total)
    q[~visited] = v_mix
    return q


def old_improved(self, node):
    sigma = (self.c_visit + node.N.max()) * self.c_scale
    x = node.logits + sigma * (old_completed_q(self, node) + 1) / 2
    p = np.exp(x - x.max())
    return p / p.sum()


def old_pick(self, node):
    pi = old_improved(self, node)
    return int(np.argmax(pi - node.N / (1 + node.N.sum())))


moves = int(sys.argv[1]) if len(sys.argv) > 1 else 30
net = Net.load("runs/g1/nets/round_066.npz")
samples = []
orig = M.Search.improved_policy
rng = random.Random(1)


def recording(self, node, total=None):
    if rng.random() < 0.2:
        snap = types.SimpleNamespace(
            N=node.N.copy(),
            W=node.W.copy(),
            prior=node.prior,
            logits=node.logits,
            value=node.value,
        )
        samples.append(snap)
    return orig(self, node, total)


M.Search.improved_policy = recording
search = M.Search(net, simulations=400, rng=random.Random(2))
for s in range(3):
    g = fresh_game(8000 + s)
    for _ in range(moves):
        if g.winning_color() is not None:
            break
        if len(g.playable_actions) == 1:
            g.execute(g.playable_actions[0])
            continue
        action, _ = search.run(g)
        g.execute(action)
M.Search.improved_policy = orig
print(
    len(samples),
    "node states; unvisited",
    sum(n.N.sum() == 0 for n in samples),
    "all visited",
    sum(bool((n.N > 0).all()) for n in samples),
)

differ = pick_differ = 0
for n in samples:
    a, b = old_improved(search, n), search.improved_policy(n)
    if a.tobytes() != b.tobytes():
        differ += 1
    if old_pick(search, n) != search.pick(n):
        pick_differ += 1
print(f"improved_policy differs on {differ}, pick differs on {pick_differ}")
for name, fn in (
    ("old improved", lambda n: old_improved(search, n)),
    ("new improved", search.improved_policy),
    ("old pick", lambda n: old_pick(search, n)),
    ("new pick", search.pick),
):
    t = min(timeit.repeat(lambda: [fn(n) for n in samples], number=3, repeat=3))
    print(f"{name}: {t / 3 / len(samples) * 1e6:.2f} us per call")
