# Workstream: efficiency

**Aim:** more self-play games, and stronger searches, from the same compute. Training data per hour is the training loop's bottleneck on the free-tier VMs.

**Owns:** `selfplay.py`, `mcts.py` (speed, not search policy), `distributed.py`, `encode.py`, `graph.py`/`net.py` inference speed, Catanatron's game engine hot paths, and `bench/`.

**First tasks:**
1. Baseline: games per hour per core and simulations per second, on the coordinator and one worker, with `bench/speed.py` and `bench/selfplay.py`. Record the commands and numbers in your progress notes.
2. Profile one self-play game (cProfile or pyinstrument) and rank the hot spots: net inference, feature encoding, state copying, legal move generation.
3. Attack the biggest one. Typical wins: batching leaf evaluations across parallel games, caching encodings, cheaper state copies, numpy inference without overhead.

**Rules for this workstream:**
- A speedup must leave play unchanged. Show it with identical moves on fixed seeds, or with a match of at least 400 games inside the noise, before landing.
- Report every gain as before → after, in games per hour per core.
