"""Sample RAM and GPU memory on gpu-train-1 every 20 s, for E37 (12M-position sample).

Prints one line per sample: UTC time, RAM used/total MB, the biggest python RSS MB,
GPU memory used MB. Stops after --minutes.
"""

import subprocess
import sys
import time

key = "/home/owen/.ssh/colonist_worker_ed25519"
host = "owen@35.231.142.146"
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 120
remote = (
    "free -m | awk '/Mem:/{print $3\"/\"$2}'; "
    "ps -eo rss,comm --sort=-rss | awk '/python/{print int($1/1024); exit}'; "
    "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1"
)
end = time.time() + minutes * 60
peak_ram = peak_py = 0
while time.time() < end:
    r = subprocess.run(
        [
            "ssh",
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            remote,
        ],
        capture_output=True,
        text=True,
    )
    parts = r.stdout.split()
    stamp = time.strftime("%H:%M:%S", time.gmtime())
    if r.returncode == 0 and parts:
        try:
            peak_ram = max(peak_ram, int(parts[0].split("/")[0]))
            if len(parts) > 1:
                peak_py = max(peak_py, int(parts[1]))
        except ValueError:
            pass
        print(
            stamp,
            "ram",
            parts[0],
            "py_rss",
            parts[1:2],
            "gpu",
            parts[2:3],
            "| peak ram",
            peak_ram,
            "peak py",
            peak_py,
            flush=True,
        )
    else:
        print(stamp, "ssh failed", r.returncode, r.stderr.strip()[:120], flush=True)
    time.sleep(20)
