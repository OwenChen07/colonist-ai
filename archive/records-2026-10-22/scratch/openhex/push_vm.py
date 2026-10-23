"""Copy the main checkout's code and round 73's net to openhex-bot-1
(what scripts/vm/openhex/setup.sh does, minus the key, which is already there).

Usage: .venv/bin/python data/orchestrator/scratch/openhex/push_vm.py
"""

import subprocess

MAIN = "/home/owen/colonist-ai"
HOST = "owen@10.142.0.9"
SSH = "ssh -i /home/owen/.ssh/colonist_worker_ed25519 -o BatchMode=yes -o ConnectTimeout=20"
R = "colonist-ai"
for cmd in (
    [
        "rsync",
        "-a",
        "--exclude",
        "*.egg-info",
        "--exclude",
        "__pycache__",
        "-e",
        SSH,
        f"{MAIN}/catanatron",
        f"{MAIN}/scripts",
        f"{MAIN}/pyproject.toml",
        f"{HOST}:{R}/",
    ],
    [
        "rsync",
        "-a",
        "-e",
        SSH,
        f"{MAIN}/runs/g1/nets/round_152.npz",
        f"{HOST}:{R}/runs/g1/nets/round_152.npz",
    ],
):
    subprocess.run(cmd, check=True)
print("pushed code and round_152.npz")
