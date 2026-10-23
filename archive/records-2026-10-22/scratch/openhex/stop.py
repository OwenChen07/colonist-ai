"""Stop the OpenHex client started by launch.py (PID in jobs/openhex.pid)."""

import os
import signal

PID = "/home/owen/colonist-ai/data/orchestrator/jobs/openhex.pid"
pid = int(open(PID).read())
with open(f"/proc/{pid}/cmdline", "rb") as f:
    cmd = f.read().replace(b"\0", b" ").decode()
assert "catanatron.openhex.client" in cmd, cmd
os.kill(pid, signal.SIGINT)  # KeyboardInterrupt -> finally leaves the queue
print(f"sent SIGINT to {pid}: {cmd}")
