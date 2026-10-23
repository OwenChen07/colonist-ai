"""Sync from the VM every minute until its log shows N finished games (max 35 min)."""

import subprocess
import sys
import time

N = int(sys.argv[1])
LOG = "/home/owen/colonist-ai/data/openhex/vm-client.log"
SYNC = [
    "/home/owen/colonist-ai/.venv/bin/python",
    "/home/owen/colonist-ai/data/orchestrator/scratch/openhex/sync_vm.py",
    "--once",
]
for _ in range(35):
    subprocess.run(SYNC, stdout=subprocess.DEVNULL)
    done = sum(" over: " in line for line in open(LOG))
    if done >= N:
        break
    time.sleep(60)
print(f"{done} games over")
