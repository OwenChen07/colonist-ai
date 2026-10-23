"""Runs rec_sizes.py-style parts of several sizes one after another and prints
seconds per game for each. usage: part_sizes.py <size> ..."""

import os
import subprocess
import sys

here = os.path.dirname(__file__)
for size in sys.argv[1:]:
    out = subprocess.run(
        [
            sys.executable,
            f"{here}/rec_sizes.py",
            f"{here}/rec_out{size}",
            size,
            f"{here}/rec_sizes{size}.pkl",
        ],
        capture_output=True,
        text=True,
    )
    print(f"--- part of {size} games", flush=True)
    print(out.stdout, out.stderr[-2000:], flush=True)
