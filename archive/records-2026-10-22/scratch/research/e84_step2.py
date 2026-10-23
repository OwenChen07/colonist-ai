"""E84 step 2: step 1's fine-tune of round 189 with each late game's start row
repeated (data/orchestrator/e84/starts listed K times) so starts make about
10% of the positions drawn (e84_starts2.py: K 100).

    .venv/bin/python data/orchestrator/scratch/research/e84_step2.py

Waits until training has taken the PC out of workers.env (as E85 did), then
under gpu.lock (e84-starts) trains as step 1: 2 epochs, lr 3e-4, EMA 0.9997,
4M positions drawn, seed 84, on rounds 182-199 + people's value rows x12 +
the late games x1 + the start rows xK. Outputs in data/orchestrator/e84/step2/.
"""

import os
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e54_dist import PC, ROOT, RPY, lock, rsync, ssh, unlock  # noqa: E402
from e85_value import wait_for_pc  # noqa: E402

R189 = "runs/g1/nets/round_189.npz"
E84 = os.path.join(ROOT, "data/orchestrator/e84")
OUT = os.path.join(E84, "step2")
ROUNDS = range(182, 200)
K = 100
WORK = "/home/owen/e84s2"
TAG = "e84-starts"


def say(text):
    print(time.strftime("[%b %d %H:%M:%S] ") + text, flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    wait_for_pc()
    lock(TAG)
    try:
        say("gpu.lock held; copying data")
        ssh(PC, f"rm -rf {WORK}; mkdir -p {WORK}/train")
        rsync(os.path.join(ROOT, R189), f"{PC}:{WORK}/init.npz")
        shards = ["--include=shard_*.npz", "--exclude=*"]
        rsync(E84 + "/data/", f"{PC}:{WORK}/train/late/", *shards)
        rsync(E84 + "/starts/", f"{PC}:{WORK}/train/starts/", *shards)
        human = os.path.join(ROOT, "data/human/train/value") + "/"
        rsync(human, f"{PC}:{WORK}/train/human/")
        folders = []
        for r in ROUNDS:
            source = os.path.join(ROOT, f"runs/g1/round_{r:03d}/data") + "/"
            rsync(source, f"{PC}:{WORK}/train/r{r}/", *shards)
            folders.append(f"{WORK}/train/r{r}")
        folders += [f"{WORK}/train/human"] * 12 + [f"{WORK}/train/late"]
        folders += [f"{WORK}/train/starts"] * K
        cmd = [RPY, "-m", "catanatron.colonist.train", *folders]
        cmd += ["--out", f"{WORK}/out", "--init", f"{WORK}/init.npz"]
        cmd += ["--epochs", "2", "--lr", "3e-4", "--ema", "0.9997"]
        cmd += ["--max-positions", "4000000", "--device", "cuda", "--seed", "84"]
        say("training: " + " ".join(cmd[3 + len(folders) :]))
        out = ssh(PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1", False)
        with open(os.path.join(OUT, "train.txt"), "w") as f:
            f.write(out)
        print(out[-3000:], flush=True)
        for name in ("net.npz", "last.npz", "log.csv"):
            rsync(f"{PC}:{WORK}/out/{name}", os.path.join(OUT, name))
        ssh(PC, f"rm -rf {WORK}")
    finally:
        unlock(TAG)
        say("gpu.lock released")
    say("done")


if __name__ == "__main__":
    main()
