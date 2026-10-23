"""Self time per source file from a cProfile dump. usage: prof_by_file.py <prof>"""

import collections
import pstats
import sys

st = pstats.Stats(sys.argv[1])
by = collections.Counter()
for (fn, line, name), (cc, nc, tt, ct, callers) in st.stats.items():
    key = fn.split("catanatron/catanatron/")[-1] if "catanatron/" in fn else fn
    if key.startswith("~") or key.startswith("<"):
        key = "builtins/C"
    by[key] += tt
total = sum(by.values())
print(f"total self time {total:.1f}s")
for k, v in by.most_common(25):
    print(f"{v:7.2f}s {100 * v / total:5.1f}%  {k}")
