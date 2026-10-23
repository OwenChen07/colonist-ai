import sys

import numpy as np

z = np.load(sys.argv[1])
total = 0
for k in z.files:
    a = z[k]
    total += a.nbytes
    print(k, a.shape, a.dtype, a.nbytes)
n = len(z[z.files[0]])
print("bytes", total, "rows", n)
