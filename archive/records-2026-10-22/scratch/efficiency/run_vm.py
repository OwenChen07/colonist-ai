"""Run one read-only command on a worker: run_vm.py <user@host> '<command>'"""

import subprocess
import sys

r = subprocess.run(
    [
        "ssh",
        "-i",
        "/home/owen/.ssh/colonist_worker_ed25519",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        sys.argv[1],
        sys.argv[2],
    ],
    capture_output=True,
    text=True,
    timeout=120,
)
print(r.stdout[-4000:], r.stderr[-1000:], "exit", r.returncode)
