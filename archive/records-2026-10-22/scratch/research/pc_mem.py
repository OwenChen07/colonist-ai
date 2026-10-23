"""Show the PC's memory, top processes, gpu.lock and recent OOM kills."""

import os
import subprocess

KEY = os.path.expanduser("~/.ssh/colonist_worker_ed25519")
cmd = (
    "free -m; echo; ps -eo pid,ppid,lstart,rss,args --sort=pid | grep -E 'python|train' | grep -v grep | cut -c1-220; echo;"
    " cat /home/owen/colonist-ai/gpu.lock 2>/dev/null; echo;"
    " du -sh /home/owen/e54/iter-2/data 2>/dev/null; nvidia-smi --query-gpu=memory.used,memory.total --format=csv;"
    " journalctl -k --since '-30 min' 2>/dev/null | grep -i -E 'oom|killed' | tail -5"
)
r = subprocess.run(
    [
        "ssh",
        "-i",
        KEY,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        "owen@100.125.136.114",
        cmd,
    ],
    capture_output=True,
    text=True,
)
print(r.stdout, r.stderr)
