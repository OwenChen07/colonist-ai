"""E85 step 1: value-only fine-tune of E83's step 50 on production's window.

    .venv/bin/python data/orchestrator/scratch/research/e85_value.py

Waits until training has taken the PC out of workers.env and it has the
memory for a 4M-position load, then under gpu.lock runs train.py with the
policy loss off (--policy-weight 0) and a KL of 0.02 to step 50's own policy,
1 epoch at lr 3e-5, on production's self-play rounds 182-199 plus people's
value rows listed 12 times (HUMAN_REPEAT), 4M positions drawn. The net ends
up in data/orchestrator/e85/net.npz (and last.npz, log.csv, train.txt).
"""

import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e54_dist import PC, ROOT, RPY, SSH, lock, rsync, ssh, unlock  # noqa: E402

STEP50 = "data/orchestrator/e54/run1/iter-50/net.npz"
OUT = os.path.join(ROOT, "data/orchestrator/e85")
ROUNDS = range(182, 200)
MIN_FREE_KB = 6_000_000
WORK = "/home/owen/e85"


def say(text):
    print(time.strftime("[%b %d %H:%M:%S] ") + text, flush=True)


def wait_for_pc():
    said = False
    while True:
        listed = (
            PC.split("@")[1]
            in open(os.path.join(ROOT, "scripts/vm/workers.env")).read().split("\n")[0]
        )
        free = ssh(PC, "awk '/MemAvailable/ {print $2}' /proc/meminfo", False)
        free = int(free.strip() or 0)
        if not listed and free >= MIN_FREE_KB:
            say(f"PC free: out of workers.env, {free // 1000} MB available")
            return
        if not said:
            say(f"waiting for the PC: listed {listed}, {free // 1000} MB available")
            said = True
        time.sleep(60)


def main():
    os.makedirs(OUT, exist_ok=True)
    wait_for_pc()
    tag = "e85-value"
    lock(tag)
    try:
        say("gpu.lock held; syncing code (as sync_workers.sh does)")
        # The PC left workers.env before c78b95c, so it missed that sync.
        excl = ["--exclude", "*.egg-info", "--exclude", "__pycache__"]
        subprocess.run(
            [
                "rsync",
                "-a",
                *excl,
                "-e",
                SSH,
                "catanatron",
                "pyproject.toml",
                f"{PC}:{ROOT}/",
            ],
            cwd=ROOT,
            check=True,
        )
        helptext = ssh(
            PC, f"cd {ROOT}; {RPY} -m catanatron.colonist.train --help", False
        )
        if "--policy-weight" not in helptext or "--policy-kl" not in helptext:
            raise SystemExit("PC's train.py still lacks --policy-weight/--policy-kl")
        say("PC's train.py has --policy-weight and --policy-kl; copying data")
        ssh(PC, f"rm -rf {WORK}; mkdir -p {WORK}/data {WORK}/human")
        rsync(os.path.join(ROOT, STEP50), f"{PC}:{WORK}/init.npz")
        rsync(os.path.join(ROOT, "data/human/train/value") + "/", f"{PC}:{WORK}/human/")
        folders = []
        for r in ROUNDS:
            source = os.path.join(ROOT, f"runs/g1/round_{r:03d}/data") + "/"
            rsync(
                source,
                f"{PC}:{WORK}/data/r{r}/",
                "--include=shard_*.npz",
                "--exclude=*",
            )
            folders.append(f"{WORK}/data/r{r}")
        folders += [f"{WORK}/human"] * 12
        cmd = [RPY, "-m", "catanatron.colonist.train", *folders]
        cmd += ["--out", f"{WORK}/out", "--init", f"{WORK}/init.npz"]
        cmd += ["--policy-weight", "0", "--policy-kl", "0.02"]
        cmd += ["--epochs", "1", "--lr", "3e-5", "--max-positions", "4000000"]
        cmd += ["--device", "cuda", "--seed", "85"]
        say("training: " + " ".join(cmd[3:]))
        out = ssh(PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1", False)
        with open(os.path.join(OUT, "train.txt"), "w") as f:
            f.write(out)
        print(out, flush=True)
        for name in ("net.npz", "last.npz", "log.csv"):
            rsync(f"{PC}:{WORK}/out/{name}", os.path.join(OUT, name))
        ssh(PC, f"rm -rf {WORK}")
    finally:
        unlock(tag)
        say("gpu.lock released")
    say("done")


if __name__ == "__main__":
    main()
