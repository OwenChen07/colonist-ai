"""Read-only: journal lines on a bot VM. vm_journal.py IP SINCE_UTC UNTIL_UTC [GREP]"""

import subprocess
import sys

ip, since, until = sys.argv[1], sys.argv[2], sys.argv[3]
pat = sys.argv[4] if len(sys.argv) > 4 else "sudo|COMMAND|colonist"
cmd = f"sudo journalctl --utc --no-pager --since '{since}' --until '{until}' | grep -E '{pat}' | tail -40"
r = subprocess.run(
    [
        "ssh",
        "-i",
        "/home/owen/.ssh/colonist_worker_ed25519",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        f"ubuntu@{ip}",
        cmd,
    ],
    capture_output=True,
    text=True,
)
print(r.stdout[-6000:], r.stderr[-500:])
