"""Compares npz files key by key, bitwise. usage: cmp_npz.py a.npz b.npz [c.npz ...]"""

import sys

import numpy as np

ref = np.load(sys.argv[1])
for other in sys.argv[2:]:
    o = np.load(other)
    bad = [
        k for k in ref.files if k not in o.files or ref[k].tobytes() != o[k].tobytes()
    ]
    print(
        other,
        "identical" if not bad else f"differs on {bad}",
        len(ref["seed"]),
        "games",
        len(ref["pi"]),
        "rows",
    )
