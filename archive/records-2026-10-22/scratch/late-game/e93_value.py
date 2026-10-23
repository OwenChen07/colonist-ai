"""E93 (late-game fix 1): E85 step 1's value pass with our live games against
2100+ people weighted up.

    .venv/bin/python data/orchestrator/scratch/late-game/e93_value.py

Identical to research's e85_value.py (step 50, policy loss off, KL 0.02 to its
own policy, 1 epoch, lr 3e-5, 4M positions drawn, rounds 182-199 + people's
value rows x12, seed 85) except that the training split's value shards of
games against 2100+ people (e93/train2100.txt, from fix1_count.py) are listed
EXTRA more times, so they are about 16% of what is drawn instead of about 5%.
E85 step 1 is the paired control. Waits for 7 AM Toronto (research's window)
and for the PC to be out of workers.env with the memory free, then holds
gpu.lock (tag e93-value). Net to data/orchestrator/e93/net.npz.
"""

import calendar
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../research")
)
from e54_dist import PC, ROOT, RPY, SSH, lock, rsync, ssh, unlock  # noqa: E402

STEP50 = "data/orchestrator/e54/run1/iter-50/net.npz"
OUT = os.path.join(ROOT, "data/orchestrator/e93")
LIST = os.path.join(OUT, "train2100.txt")
ROUNDS = range(182, 200)
EXTRA = 28
MIN_FREE_KB = 6_000_000
NOT_BEFORE_UTC = (2026, 10, 23, 11, 0)  # 7 AM Toronto
WORK = "/home/owen/e93"


def say(text):
    print(time.strftime("[%b %d %H:%M:%S] ") + text, flush=True)


def wait_for_window():
    start = calendar.timegm((*NOT_BEFORE_UTC, 0, 0, 0, 0))
    if time.time() < start:
        say("waiting for 7 AM Toronto (research's GPU window)")
        while time.time() < start:
            time.sleep(60)


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
    names = [os.path.basename(p) for p in open(LIST).read().split()]
    say(f"{len(names)} shards of 2100+ games, listed {EXTRA} extra times")
    wait_for_window()
    wait_for_pc()
    tag = "e93-value"
    lock(tag)
    try:
        say("gpu.lock held; syncing code (as sync_workers.sh does)")
        excl = ["--exclude", "*.egg-info", "--exclude", "__pycache__"]
        subprocess.run(
            ["rsync", "-a", *excl, "-e", SSH, "catanatron", "pyproject.toml"]
            + [f"{PC}:{ROOT}/"],
            cwd=ROOT,
            check=True,
        )
        helptext = ssh(
            PC, f"cd {ROOT}; {RPY} -m catanatron.colonist.train --help", False
        )
        if "--policy-weight" not in helptext or "--policy-kl" not in helptext:
            raise SystemExit("PC's train.py lacks --policy-weight/--policy-kl")
        ssh(PC, f"rm -rf {WORK}; mkdir -p {WORK}/data {WORK}/human {WORK}/h2100")
        rsync(os.path.join(ROOT, STEP50), f"{PC}:{WORK}/init.npz")
        value = os.path.join(ROOT, "data/human/train/value") + "/"
        rsync(value, f"{PC}:{WORK}/human/")
        with open(os.path.join(OUT, "names.txt"), "w") as f:
            f.write("\n".join(names) + "\n")
        rsync(
            value,
            f"{PC}:{WORK}/h2100/",
            "--files-from",
            os.path.join(OUT, "names.txt"),
        )
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
        folders += [f"{WORK}/human"] * 12 + [f"{WORK}/h2100"] * EXTRA
        cmd = [RPY, "-m", "catanatron.colonist.train", *folders]
        cmd += ["--out", f"{WORK}/out", "--init", f"{WORK}/init.npz"]
        cmd += ["--policy-weight", "0", "--policy-kl", "0.02"]
        cmd += ["--epochs", "1", "--lr", "3e-5", "--max-positions", "4000000"]
        cmd += ["--device", "cuda", "--seed", "85"]
        say(
            f"training: 18 rounds + human x12 + h2100 x{EXTRA}, rest "
            + " ".join(cmd[-14:])
        )
        out = ssh(PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1", False)
        with open(os.path.join(OUT, "train.txt"), "w") as f:
            f.write(out)
        print(out[-3000:], flush=True)
        for name in ("net.npz", "last.npz", "log.csv"):
            rsync(f"{PC}:{WORK}/out/{name}", os.path.join(OUT, name))
        ssh(PC, f"rm -rf {WORK}")
    finally:
        unlock(tag)
        say("gpu.lock released")
    say("done")


if __name__ == "__main__":
    main()
