import glob
import sys

import numpy as np

p = sorted(glob.glob("data/human/holdout/value/shard_*.npz"))[0]
with np.load(p) as z:
    for k in z.files:
        a = z[k]
        print(k, a.shape, a.dtype, a.ravel()[:8])
sys.path.insert(0, "data/orchestrator/scratch/training")
src = open("data/orchestrator/scratch/training/human2100.py").read()
print(src[src.find("def per_game_sq") :][:1800])
