"""Pool E59's rows-SEED.npy files: per sigma scale, mean and 95% interval of
KL(b||a), top-1 agreement, KL(a||prior) and entropy over all positions."""

import glob
import sys

import numpy as np

SCALES = (1.0, 0.5, 0.25, 0.1)
NAMES = ("KL(b||a)", "top1 agree", "KL(a||prior)", "entropy")

files = sorted(
    glob.glob(sys.argv[1] if len(sys.argv) > 1 else "data/orchestrator/e59/rows-*.npy")
)
arrs = [np.load(f) for f in files]
print(f"{len(files)} files: {files}")
pooled = np.concatenate(arrs, axis=1)  # [scale, pos, 4]
for i, sc in enumerate(SCALES):
    a = pooled[i]
    parts = []
    for j, name in enumerate(NAMES):
        m = a[:, j].mean()
        h = 1.96 * a[:, j].std(ddof=1) / np.sqrt(len(a))
        parts.append(f"{name} {m:.4f} ±{h:.4f}")
    print(f"scale {sc}: n {len(a)}  " + "  ".join(parts))
base = pooled[0]
for i, sc in enumerate(SCALES[1:], 1):
    a = pooled[i]
    print(
        f"scale {sc} vs 1.0: disagreement {1 - a[:, 1].mean():.3f} vs {1 - base[:, 1].mean():.3f}, "
        f"KL to prior kept {a[:, 2].mean() / base[:, 2].mean():.1%}"
    )
