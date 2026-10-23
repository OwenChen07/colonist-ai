"""Wait until a new GCP worker's venv exists (startup script done)."""

import subprocess
import sys
import time

host = sys.argv[1]
deadline = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 900)
key = "/home/owen/.ssh/colonist_worker_ed25519"
while time.time() < deadline:
    r = subprocess.run(
        [
            "ssh",
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            host,
            "test -x colonist-ai/.venv/bin/python && colonist-ai/.venv/bin/python -c 'import numpy; print(numpy.__version__)'",
        ],
        capture_output=True,
        text=True,
    )
    print(
        time.strftime("%H:%M:%S"),
        r.returncode,
        r.stdout.strip(),
        r.stderr.strip()[-200:],
        flush=True,
    )
    if r.returncode == 0:
        sys.exit(0)
    time.sleep(20)
sys.exit(1)
