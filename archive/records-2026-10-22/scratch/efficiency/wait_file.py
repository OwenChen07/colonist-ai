"""Wait until a file contains a marker string (or time out)."""

import sys
import time

path, marker = sys.argv[1], sys.argv[2]
deadline = time.time() + float(sys.argv[3] if len(sys.argv) > 3 else 1800)
while time.time() < deadline:
    try:
        if marker in open(path).read():
            print(open(path).read())
            sys.exit(0)
    except FileNotFoundError:
        pass
    time.sleep(15)
print("timed out")
sys.exit(1)
