"""Run one shell command on openhex-bot-1 and print its output.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/vm.py 'COMMAND'
"""

import subprocess
import sys

HOST = "owen@10.142.0.9"
SSH = [
    "ssh",
    "-i",
    "/home/owen/.ssh/colonist_worker_ed25519",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=20",
    HOST,
]
r = subprocess.run(SSH + [sys.argv[1]], capture_output=True, text=True)
print(r.stdout, end="")
print(r.stderr, end="", file=sys.stderr)
sys.exit(r.returncode)
