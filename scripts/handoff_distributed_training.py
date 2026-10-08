"""Switch a live self-play loop to two VMs at the next round boundary.

The existing loop remains untouched through its current round.  Once state.json
advances to a later round's selfplay step, stop that loop, wait for its workers
to exit, and resume the same command with COLONIST_WORKER_HOST enabled.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def say(message):
    print(message, flush=True)


def command(pid):
    try:
        return [
            part.decode()
            for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if part
        ]
    except FileNotFoundError:
        return None


def descendants(pid):
    rows = subprocess.check_output(["ps", "-eo", "pid=,ppid="], text=True).splitlines()
    by_parent = {}
    for row in rows:
        child, parent = (int(value) for value in row.split())
        by_parent.setdefault(parent, []).append(child)
    found = set()
    pending = [pid]
    while pending:
        for child in by_parent.get(pending.pop(), ()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # A reparented worker can briefly remain as a zombie; it cannot write
        # another shard and should not block the replacement loop.
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


def wait_gone(pids, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        remaining = {pid for pid in pids if alive(pid)}
        if not remaining:
            return True
        time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--after-round", type=int, default=7)
    parser.add_argument("--worker-host", required=True)
    parser.add_argument("--remote-workers", type=int, default=2)
    args = parser.parse_args()
    state_path = args.root / args.run / "state.json"
    log_path = args.root / f"{args.run}.log"
    old_command = command(args.old_pid)
    if (
        not old_command
        or "catanatron.colonist.loop" not in old_command
        or args.run not in old_command
    ):
        raise SystemExit("old PID does not match the expected training loop")
    say(f"watching {state_path} for the round {args.after_round + 1} handoff")
    while True:
        state = json.loads(state_path.read_text())
        if state["round"] > args.after_round and state["step"] == "selfplay":
            break
        if not alive(args.old_pid):
            raise SystemExit("training loop exited before the handoff")
        time.sleep(2)
    children = descendants(args.old_pid)
    say(f"round {state['round']} self-play reached; stopping original training loop")
    os.kill(args.old_pid, signal.SIGTERM)
    if not wait_gone({args.old_pid}, 90):
        raise SystemExit("original training loop did not exit; no replacement started")
    remaining = {pid for pid in children if alive(pid)}
    if remaining:
        for pid in remaining:
            os.kill(pid, signal.SIGTERM)
        if not wait_gone(remaining, 30):
            raise SystemExit("old self-play workers remained; no replacement started")
    env = os.environ.copy()
    env["COLONIST_WORKER_HOST"] = args.worker_host
    env["COLONIST_REMOTE_WORKERS"] = str(args.remote_workers)
    with log_path.open("a") as log:
        log.write(f"\n=== distributed handoff at round {state['round']} ===\n")
        log.flush()
        new = subprocess.Popen(
            old_command,
            cwd=args.root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    say(f"resumed two-VM training as PID {new.pid}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        say(f"handoff failed: {error}")
        sys.exit(1)
