"""Runs ab_parts.py for several part sizes at the same time (same load) on the
same seeds. usage: ab_parts_all.py <first> <total> <part> ..."""

import os
import subprocess
import sys

here = os.path.dirname(__file__)
first, total = sys.argv[1], sys.argv[2]
procs = {
    p: subprocess.Popen(
        [
            sys.executable,
            f"{here}/ab_parts.py",
            f"{here}/abp{p}_{first}",
            first,
            total,
            p,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for p in sys.argv[3:]
}
for p, proc in procs.items():
    out, _ = proc.communicate()
    print(f"--- part size {p}\n{out[-3000:]}", flush=True)
