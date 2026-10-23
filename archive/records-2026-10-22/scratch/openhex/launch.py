"""Start the OpenHex client detached (like nohup ... &), log to jobs/openhex.log.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/launch.py NET --games N [--core C]
"""

import os
import sys

ROOT = "/home/owen/colonist-ai"
LOG = f"{ROOT}/data/orchestrator/jobs/openhex.log"
PID = f"{ROOT}/data/orchestrator/jobs/openhex.pid"

if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
os.chdir(ROOT)
os.makedirs(os.path.dirname(LOG), exist_ok=True)
fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
with open(PID, "w") as f:
    f.write(f"{os.getpid()}\n")
os.nice(10)
os.execv(
    f"{ROOT}/.venv/bin/python",
    [f"{ROOT}/.venv/bin/python", "-u", "-m", "catanatron.openhex.client"]
    + sys.argv[1:],
)
