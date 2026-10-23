import re
import sys
import time

# usage: waitlog.py LOG REGEX SECONDS -> prints the first new matching line, or "timeout"
path, pattern, secs = sys.argv[1], re.compile(sys.argv[2]), float(sys.argv[3])
with open(path, "rb") as f:
    start = len(f.read())
new = ""
deadline = time.time() + secs
while time.time() < deadline:
    with open(path, "rb") as f:
        f.seek(start)
        new = f.read().decode(errors="replace")
    for line in new.splitlines():
        if pattern.search(line):
            print("MATCH:", line)
            print(new[-3000:])
            sys.exit(0)
    time.sleep(20)
print("timeout")
print(new[-1500:])
