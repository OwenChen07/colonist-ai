"""Start a job script detached (own session), log and pid under data/orchestrator/jobs.

    .venv/bin/python data/orchestrator/scratch/research/launch.py NAME
runs data/orchestrator/jobs/NAME-run.sh -> jobs/NAME.log, pid in jobs/NAME.pid.
"""

import os
import subprocess
import sys

ROOT = "/home/owen/colonist-ai"
name = sys.argv[1]
jobs = os.path.join(ROOT, "data/orchestrator/jobs")
with open(os.path.join(jobs, f"{name}.log"), "a") as log:
    p = subprocess.Popen(
        ["bash", os.path.join(jobs, f"{name}-run.sh")],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
with open(os.path.join(jobs, f"{name}.pid"), "w") as f:
    f.write(f"{p.pid}\n")
print(f"{name}: pid {p.pid}")
