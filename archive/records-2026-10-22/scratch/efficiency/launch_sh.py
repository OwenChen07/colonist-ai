"""Start a job script detached: launch_sh.py SCRIPT NAME [ARGS...] -> jobs/NAME.log, jobs/NAME.pid."""

import subprocess
import sys
from pathlib import Path

script, name = sys.argv[1], sys.argv[2]
jobs = Path("/home/owen/colonist-ai/data/orchestrator/jobs")
log = open(jobs / f"{name}.log", "ab")
p = subprocess.Popen(
    ["bash", script, *sys.argv[3:]],
    stdout=log,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
(jobs / f"{name}.pid").write_text(f"{p.pid}\n")
print(p.pid)
