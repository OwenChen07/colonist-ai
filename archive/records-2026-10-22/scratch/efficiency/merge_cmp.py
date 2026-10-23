"""Merges part files into one shard and compares it to a reference shard, key by
key: exact bytes, else the largest difference. usage: merge_cmp.py ref.npz out.npz part ...
"""

import sys

import numpy as np

from catanatron.colonist.selfplay import merge_parts

ref_path, out, parts = sys.argv[1], sys.argv[2], sys.argv[3:]
merge_parts(out, parts)
ref, o = np.load(ref_path), np.load(out)
for k in ref.files:
    a, b = ref[k], o[k]
    if a.shape != b.shape:
        print(k, "shape", a.shape, b.shape)
    elif a.tobytes() == b.tobytes():
        print(k, "identical")
    else:
        d = np.abs(a.astype(np.float64) - b.astype(np.float64))
        print(k, f"differs: {np.count_nonzero(d)} of {d.size}, max {d.max():.3g}")
