"""Run a scratch script detached on 1 core, single-threaded, niced.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/detach.py NAME SCRIPT [ARGS...]
Logs to data/orchestrator/jobs/NAME.log, pid in NAME.pid.
"""

import os
import sys

WT = "/home/owen/colonist-ai-wt/openhex"
name, script, args = sys.argv[1], sys.argv[2], sys.argv[3:]
LOG = f"{WT}/data/orchestrator/jobs/{name}.log"
PID = f"{WT}/data/orchestrator/jobs/{name}.pid"

if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
os.chdir(WT)
fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
with open(PID, "w") as f:
    f.write(f"{os.getpid()}\n")
os.nice(10)
os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
env = dict(os.environ)
for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    env[k] = "1"
py = f"{WT}/.venv/bin/python"
os.execve(py, [py, "-u", script] + args, env)
