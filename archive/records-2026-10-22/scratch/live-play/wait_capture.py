"""Poll VM 3 for bot8's first capture after a given name; report whether it carries robber_filter."""

import subprocess
import sys
import time

AFTER = sys.argv[1]  # e.g. bridge-20261022-110341.json
SSH = [
    "ssh",
    "-i",
    "/home/owen/.ssh/colonist_worker_ed25519",
    "-o",
    "BatchMode=yes",
    "ubuntu@10.128.0.10",
]
for _ in range(80):
    names = subprocess.run(
        SSH + ["ls colonist-ai/captures/bot8"], capture_output=True, text=True
    ).stdout.split()
    new = sorted(n for n in names if n.startswith("bridge-") and n > AFTER)
    if new:
        f = "colonist-ai/captures/bot8/" + new[0]
        out = subprocess.run(
            SSH + [f'grep -o \'"robber_filter": *"[a-z_0-9]*"\' {f} | sort | uniq -c'],
            capture_output=True,
            text=True,
        ).stdout
        print(new[0], "robber_filter:", out.strip() or "MISSING")
        break
    time.sleep(30)
else:
    print("no new capture in 40 min")
