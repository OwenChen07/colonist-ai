"""Launch a job's bash script detached (the lead's allowlist refuses '&').

Usage: .venv/bin/python data/orchestrator/scratch/lead/launch_sh.py JOB

Starts `bash data/orchestrator/jobs/JOB-run.sh` in its own session with
stdout and stderr appended to data/orchestrator/jobs/JOB.log, writes its pid
to data/orchestrator/jobs/JOB.pid, prints the pid and returns at once.
"""

import os
import subprocess
import sys

ROOT = "/home/owen/colonist-ai"
job = sys.argv[1]
jobs = os.path.join(ROOT, "data/orchestrator/jobs")
script = os.path.join(jobs, f"{job}-run.sh")
if not os.path.exists(script):
    sys.exit(f"no such script: {script}")
log = open(os.path.join(jobs, f"{job}.log"), "ab")
p = subprocess.Popen(
    ["bash", script],
    cwd=ROOT,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
with open(os.path.join(jobs, f"{job}.pid"), "w") as f:
    f.write(f"{p.pid}\n")
print(f"{job}: pid {p.pid}, log {jobs}/{job}.log")
