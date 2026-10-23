"""Detached: every 5 min, copy openhex-bot-1's games and log to the coordinator
(what scripts/vm/openhex/sync_games.sh HOST --loop does).
Log jobs/openhex-sync.log, pid jobs/openhex-sync.pid.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/sync_vm.py [--once]
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = "/home/owen/colonist-ai"
HOST = "owen@10.142.0.9"
SSH = "ssh -i /home/owen/.ssh/colonist_worker_ed25519 -o BatchMode=yes -o ConnectTimeout=20"
JOBS = f"{ROOT}/data/orchestrator/jobs"


def sync():
    a = subprocess.run(
        [
            "rsync",
            "-a",
            "--update",
            "-e",
            SSH,
            f"{HOST}:colonist-ai/data/openhex/games/",
            f"{ROOT}/data/openhex/games/",
        ]
    ).returncode
    b = subprocess.run(
        [
            "rsync",
            "-a",
            "-e",
            SSH,
            f"{HOST}:colonist-ai/data/openhex/client.log",
            f"{ROOT}/data/openhex/vm-client.log",
        ]
    ).returncode
    now = datetime.now(ZoneInfo("America/Toronto")).strftime("%b %d %I:%M %p")
    print(
        f"{now} {'synced' if a == b == 0 else f'could not sync ({a}, {b})'}", flush=True
    )


if "--once" in sys.argv:
    sync()
    sys.exit(0)
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
fd = os.open(f"{JOBS}/openhex-sync.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
with open(f"{JOBS}/openhex-sync.pid", "w") as f:
    f.write(f"{os.getpid()}\n")
while True:
    sync()
    time.sleep(300)
