"""Live value MSE by phase on data/human/holdout/value for each net given.

.venv/bin/python data/orchestrator/scratch/research/live_mse.py NET ...
"""

import sys

from catanatron.colonist.calibrate import calibrate, describe, load_value_shards
from catanatron.colonist.net import Net

data = load_value_shards(["/home/owen/colonist-ai/data/human/holdout/value"])
for path in sys.argv[1:]:
    print(f"{path}: {describe(calibrate(Net.load(path), data))}", flush=True)
