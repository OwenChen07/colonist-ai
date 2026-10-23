"""Wait until PATH holds TEXT at least COUNT times (or MAX seconds pass), then print it.
Usage: wait_grep.py PATH TEXT [COUNT [MAX]]"""

import os
import sys
import time

path, text = sys.argv[1], sys.argv[2]
count = int(sys.argv[3]) if len(sys.argv) > 3 else 1
limit = float(sys.argv[4]) if len(sys.argv) > 4 else 2100
start = time.time()
while time.time() - start < limit:
    if os.path.exists(path) and open(path).read().count(text) >= count:
        time.sleep(2)
        break
    time.sleep(20)
print(open(path).read() if os.path.exists(path) else "missing")
