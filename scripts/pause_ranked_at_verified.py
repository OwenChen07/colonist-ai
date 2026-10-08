"""Pause a ranked runner only after its next verified capture is logged.

This is an operator tool for a planned bridge upgrade. It never touches the
bridge or browser and never pauses on an unverified or failed game.
"""

import argparse
import os
import signal
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("log", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    offset = args.log.stat().st_size
    partial = ""
    while time.monotonic() < deadline:
        try:
            os.kill(args.pid, 0)
        except ProcessLookupError:
            print("runner exited before boundary", flush=True)
            return 1
        with args.log.open(errors="replace") as stream:
            stream.seek(offset)
            part = stream.read()
            offset = stream.tell()
        if part:
            partial += part
            *lines, partial = partial.split("\n")
            for line in lines:
                if "verified ranked capture:" not in line:
                    continue
                os.kill(args.pid, signal.SIGSTOP)
                print(f"paused runner {args.pid} after verified capture", flush=True)
                return 0
        time.sleep(0.05)
    print("no verified boundary before timeout", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
