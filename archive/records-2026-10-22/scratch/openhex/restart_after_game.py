"""Wait (max 60 min) until the VM's current game is over, then restart the
OpenHex client there and lift the pause (the pause file was set before).

Usage: restart_after_game.py GAME_ID_PREFIX
"""

import subprocess
import sys
import time

VM = [
    "/home/owen/colonist-ai/.venv/bin/python",
    "/home/owen/colonist-ai/data/orchestrator/scratch/openhex/vm.py",
]
GAME = sys.argv[1]
LOG = "~/colonist-ai/data/openhex/client.log"


def vm(cmd):
    return subprocess.run(VM + [cmd], capture_output=True, text=True).stdout


for _ in range(60):
    if f"[{GAME}] over:" in vm(f"tail -20 {LOG}"):
        break
    time.sleep(60)
else:
    print("game not over after 60 min; pause file left in place")
    sys.exit(1)
time.sleep(5)
print(
    vm(
        "sudo systemctl restart openhex-client; sleep 5; rm -f ~/colonist-ai/data/openhex/pause; "
        f"sleep 10; systemctl is-active openhex-client; tail -5 {LOG}"
    )
)
