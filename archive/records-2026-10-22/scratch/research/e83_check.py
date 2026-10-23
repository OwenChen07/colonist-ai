"""E83 helper: run a read-only shell command on a host over the worker key.

.venv/bin/python data/orchestrator/scratch/research/e83_check.py HOST 'COMMAND'
"""

import subprocess
import sys

KEY = "/home/owen/.ssh/colonist_worker_ed25519"
host, command = sys.argv[1], sys.argv[2]
r = subprocess.run(
    ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host, command],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
print(r.stdout, end="")
print(f"[exit {r.returncode}]")
