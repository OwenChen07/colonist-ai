"""E85 step 2: 10 more E54 steps from E85's net, then E85's value pass again.

    .venv/bin/python data/orchestrator/scratch/research/e85_step2.py

Owner's yes to e85-next (Oct 22, 9:22 AM Toronto). Waits for the games VM
(its "owen@IP" in data/orchestrator/e85/step2/host.txt, written when efficiency
has made it, and answering ssh) and for the PC (out of workers.env, memory
free). Then e54_dist's loop: 20,000 pg.py games a step on the VM (16
processes, run 1's frozen pool from e83/pool), the learner on the PC's GPU
under gpu.lock with the policy-gradient value weight 0 (the policy learns, the
value head gets no gradient of its own), KL to round 73 as run 1, lr 3e-5,
1 epoch. Raw vs raw against round 73 after step 10 (1000 games, as run 1).
Then step 10's net gets E85 step 1's value pass (policy loss off, KL 0.02 to
its own policy, rounds 182-199 + people's value rows x12, 4M drawn, lr 3e-5,
1 epoch, seed 85) into e85/step2/value/. Restartable: a step whose net exists
is skipped; the value pass is skipped when value/net.npz exists.
"""

import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e54_dist as D  # noqa: E402
from e54_loop import R73, ROOT, log  # noqa: E402
from e85_value import wait_for_pc  # noqa: E402

E85 = os.path.join(ROOT, "data/orchestrator/e85")
OUT = os.path.join(E85, "step2")
HOST_FILE = os.path.join(OUT, "host.txt")
POOL_DIR = os.path.join(ROOT, "data/orchestrator/e83/pool")
ITERS, GAMES, LR, KL = 10, 20000, 3e-5, 0.02
SEED = 8_520_000_000
ROUNDS = range(182, 200)
VWORK = "/home/owen/e85s2v"


def wait_for_vm():
    said = False
    while True:
        host = open(HOST_FILE).read().strip() if os.path.exists(HOST_FILE) else ""
        if host and D.ssh(host, f"test -x {D.RPY} && echo ok", False).strip() == "ok":
            log(OUT, f"games VM {host} answers")
            return host
        if not said:
            log(OUT, f"waiting for the games VM ({HOST_FILE})")
            said = True
        time.sleep(60)


def train_pg(net, data, folder, it):
    """e54_dist.train with --value-weight 0."""
    tag = f"e85s2-iter-{it}"
    D.lock(tag)
    try:
        work = f"{D.REMOTE}/iter-{it}"
        D.ssh(D.PC, f"rm -rf {D.REMOTE}/iter-*; mkdir -p {work}")
        D.rsync(net, f"{D.PC}:{D.REMOTE}/init.npz")
        D.rsync(data + "/", f"{D.PC}:{work}/data/")
        cmd = [D.RPY, "-m", "catanatron.colonist.train", f"{work}/data"]
        cmd += ["--out", work, "--init", f"{D.REMOTE}/init.npz", "--pg"]
        cmd += ["--pg-prior", D.remote(R73), "--epochs", "1"]
        cmd += ["--lr", str(LR), "--pg-kl", str(KL), "--value-weight", "0"]
        cmd += ["--device", "cuda", "--seed", str(it)]
        out = D.ssh(D.PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1")
        D.rsync(f"{D.PC}:{work}/net.npz", os.path.join(folder, "net.npz"))
        D.ssh(D.PC, f"rm -rf {work}")
    finally:
        D.unlock(tag)
    return [l for l in out.splitlines() if l.startswith("epoch")]


def value_pass(net):
    out_dir = os.path.join(OUT, "value")
    if os.path.exists(os.path.join(out_dir, "net.npz")):
        return
    os.makedirs(out_dir, exist_ok=True)
    tag = "e85s2-value"
    D.lock(tag)
    try:
        log(OUT, "value pass: copying data")
        D.ssh(D.PC, f"rm -rf {VWORK}; mkdir -p {VWORK}/data {VWORK}/human")
        D.rsync(net, f"{D.PC}:{VWORK}/init.npz")
        human = os.path.join(ROOT, "data/human/train/value") + "/"
        D.rsync(human, f"{D.PC}:{VWORK}/human/")
        folders = []
        for r in ROUNDS:
            source = os.path.join(ROOT, f"runs/g1/round_{r:03d}/data") + "/"
            shards = ["--include=shard_*.npz", "--exclude=*"]
            D.rsync(source, f"{D.PC}:{VWORK}/data/r{r}/", *shards)
            folders.append(f"{VWORK}/data/r{r}")
        folders += [f"{VWORK}/human"] * 12
        cmd = [D.RPY, "-m", "catanatron.colonist.train", *folders]
        cmd += ["--out", f"{VWORK}/out", "--init", f"{VWORK}/init.npz"]
        cmd += ["--policy-weight", "0", "--policy-kl", "0.02"]
        cmd += ["--epochs", "1", "--lr", "3e-5", "--max-positions", "4000000"]
        cmd += ["--device", "cuda", "--seed", "85"]
        log(OUT, "value pass: training")
        out = D.ssh(
            D.PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1", False
        )
        with open(os.path.join(out_dir, "train.txt"), "w") as f:
            f.write(out)
        for name in ("net.npz", "last.npz", "log.csv"):
            D.rsync(f"{D.PC}:{VWORK}/out/{name}", os.path.join(out_dir, name))
        D.ssh(D.PC, f"rm -rf {VWORK}")
        for line in out.splitlines():
            if line.startswith("epoch"):
                log(OUT, f"value pass: {line}")
    finally:
        D.unlock(tag)


def main():
    os.makedirs(OUT, exist_ok=True)
    host = wait_for_vm()
    wait_for_pc()
    log(OUT, "PC free")
    D.HOSTS, D.PER_HOST = [host], 16
    D.retry("setup", lambda: D.setup(POOL_DIR))
    net = os.path.join(E85, "net.npz")
    for it in range(1, ITERS + 1):
        folder = os.path.join(OUT, f"iter-{it}")
        new = os.path.join(folder, "net.npz")
        if not os.path.exists(new):
            data = os.path.join(folder, "data")
            shutil.rmtree(data, ignore_errors=True)
            t0 = time.time()
            first = SEED + it * 1_000_000
            lines = D.retry(f"iter {it} play", lambda: D.play(net, data, first, GAMES))
            t1 = time.time()
            log(OUT, f"iter {it}: played {GAMES} games in {t1 - t0:.0f} s")
            tail = D.retry(f"iter {it} train", lambda: train_pg(net, data, folder, it))
            log(OUT, f"iter {it}: trained in {time.time() - t1:.0f} s")
            for line in tail:
                log(OUT, f"  {line}")
            shutil.rmtree(data, ignore_errors=True)
            if it == ITERS:
                seed = 5_400_000_000 + 900_000_000  # run 1's eval seed
                line = D.retry("eval", lambda: D.evaluate(new, 1000, seed))
                log(OUT, f"iter {it}: raw vs raw r73, 1000: {line}")
        net = new
    value_pass(net)
    log(OUT, "done; games VM can be deleted")


if __name__ == "__main__":
    main()
