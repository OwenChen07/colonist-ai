"""Copy ~/colonist-ai's code, bench and the current net to a new GCP worker.

.venv/bin/python data/orchestrator/scratch/efficiency/sync_vm.py owen@10.142.0.2
"""

import subprocess
import sys

host = sys.argv[1]
key = "/home/owen/.ssh/colonist_worker_ed25519"
ssh = f"ssh -i {key} -o BatchMode=yes -o ConnectTimeout=20"
src = "/home/owen/colonist-ai"
for args in (
    ["catanatron", "pyproject.toml", "bench"],
    ["models/graph1/net.npz"],
):
    r = subprocess.run(
        [
            "rsync",
            "-a",
            "-R",
            "--exclude",
            "*.egg-info",
            "--exclude",
            "__pycache__",
            "-e",
            ssh,
        ]
        + args
        + [f"{host}:colonist-ai/"],
        cwd=src,
    )
    print(args, "exit", r.returncode, flush=True)
r = subprocess.run(
    ssh.split()
    + [
        host,
        "colonist-ai/.venv/bin/python -c 'import catanatron.colonist.selfplay as s; print(s.__file__)'; nproc",
    ],
    capture_output=True,
    text=True,
)
print(r.returncode, r.stdout, r.stderr[-300:])
