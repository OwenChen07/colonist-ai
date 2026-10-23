"""cProfile of graph forward (old dense, new forward_np) on recorded batches.

usage: fwd_prof.py old|new
"""

import os
import pickle
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import cProfile
import pstats

from catanatron.colonist import graph as G
from catanatron.colonist.net import Net

with open("data/orchestrator/scratch/efficiency/fwd_batches.pkl", "rb") as f:
    batches = pickle.load(f)[:150]
net = Net.load("runs/g1/nets/round_066.npz")
if sys.argv[1] == "old":
    fn = net.forward_rows
else:

    def fn(s, d):
        return G.forward_np(net.params, s, d, net.layers)


fn(*batches[0])
p = cProfile.Profile()
p.enable()
for s, d in batches:
    fn(s, d)
p.disable()
pstats.Stats(p).sort_stats("tottime").print_stats(18)
