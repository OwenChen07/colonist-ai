"""Compares the part files of two dirs with the same names, key by key.
usage: cmp_dirs.py <dir_a> <dir_b>"""

import os
import sys

import numpy as np

a_dir, b_dir = sys.argv[1:3]
names = sorted(n for n in os.listdir(a_dir) if n.endswith(".npz"))
assert names == sorted(n for n in os.listdir(b_dir) if n.endswith(".npz")), "names"
for name in names:
    a, b = np.load(os.path.join(a_dir, name)), np.load(os.path.join(b_dir, name))
    bad = []
    for k in a.files:
        x, y = a[k], b[k]
        if x.shape != y.shape:
            bad.append(f"{k} shape {x.shape} {y.shape}")
        elif x.tobytes() != y.tobytes():
            d = np.abs(x.astype(np.float64) - y.astype(np.float64))
            bad.append(
                f"{k}: {np.count_nonzero(d)} of {d.size} differ, max {d.max():.3g}"
            )
    print(name, "identical" if not bad else "; ".join(bad))
