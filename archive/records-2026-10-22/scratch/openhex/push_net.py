"""Copy one net to openhex-bot-1 and compare size and sha256 on both sides.

Usage: push_net.py LOCAL_PATH REMOTE_NAME   (REMOTE_NAME goes to runs/g1/nets/ on the VM)
"""

import hashlib
import os
import subprocess
import sys

HOST = "owen@10.142.0.9"
SSH = "ssh -i /home/owen/.ssh/colonist_worker_ed25519 -o BatchMode=yes -o ConnectTimeout=20"
src, name = sys.argv[1], sys.argv[2]
dst = f"colonist-ai/runs/g1/nets/{name}"
subprocess.run(["rsync", "-a", "-e", SSH, src, f"{HOST}:{dst}"], check=True)
local = (os.path.getsize(src), hashlib.sha256(open(src, "rb").read()).hexdigest())
out = subprocess.run(
    SSH.split() + [HOST, f"stat -c %s {dst}; sha256sum {dst}"],
    capture_output=True,
    text=True,
    check=True,
).stdout.split()
remote = (int(out[0]), out[1])
print("local ", local)
print("remote", remote)
print("MATCH" if local == remote else "MISMATCH")
