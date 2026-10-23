"""Runs ab_stream.py (parts of 8) beside ab_parts.py with parts of 8 and 32, at
the same time on the same seeds. usage: ab_stream_all.py <first> <total>"""

import os
import subprocess
import sys

here = os.path.dirname(__file__)
first, total = sys.argv[1], sys.argv[2]
runs = {
    "stream8w16": ["ab_stream.py", f"{here}/abs16_{first}", first, total, "8", "16"],
    "stream8w32": ["ab_stream.py", f"{here}/abs32_{first}", first, total, "8", "32"],
    "part8": ["ab_parts.py", f"{here}/abp8_{first}", first, total, "8"],
}
procs = {
    name: subprocess.Popen(
        [sys.executable, f"{here}/{argv[0]}", *argv[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for name, argv in runs.items()
}
for name, proc in procs.items():
    out, _ = proc.communicate()
    print(f"--- {name}\n{out[-3000:]}", flush=True)
print("ALL DONE", flush=True)
