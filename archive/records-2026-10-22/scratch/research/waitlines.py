"""Wait until FILE has more than N lines or SECONDS pass, then print its tail.
Usage: waitlines.py FILE N SECONDS"""

import sys
import time

path, n, deadline = sys.argv[1], int(sys.argv[2]), time.time() + float(sys.argv[3])
while time.time() < deadline:
    with open(path) as f:
        lines = f.readlines()
    if len(lines) > n:
        break
    time.sleep(15)
print("".join(lines[-3:]))
