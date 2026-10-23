import os
import sys
import time

pid = int(sys.argv[1])
deadline = time.time() + float(sys.argv[2])
while time.time() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print("exited")
        break
    time.sleep(5)
else:
    print("still running")
