"""E84 step 1: late people starts, played and learned, on E85's PC outage.

    .venv/bin/python data/orchestrator/scratch/research/e84_run.py

1. Waits for E85 to finish (its net.npz exists and gpu.lock is no longer
   e85-value), then holds gpu.lock as e84-late for the whole job.
2. Round 189 plays 20,000 games from data/orchestrator/e84/late_starts.jsonl.gz
   with production's self-play search: 16,000 on the PC (11 workers, the PC is
   out of the fleet) and 4,000 here (4 workers, niceness 19), at once.
3. Fine-tunes round 189 on the PC as production does (2 epochs, lr 3e-4,
   EMA 0.9997, 4M positions drawn) on self-play rounds 182-199 plus people's
   value rows 12 times plus the late games once. Seed 84.
Outputs in data/orchestrator/e84/: data/ (the late shards), net.npz, last.npz,
log.csv, train.txt.
"""

import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e54_dist import LOCK, PC, ROOT, RPY, lock, rsync, ssh, unlock  # noqa: E402

R189 = "runs/g1/nets/round_189.npz"
STARTS = "data/orchestrator/e84/late_starts.jsonl.gz"
OUT = os.path.join(ROOT, "data/orchestrator/e84")
DATA = os.path.join(OUT, "data")
ROUNDS = range(182, 200)
WORK = "/home/owen/e84"
SEED = 8_400_000_000
PC_GAMES, PC_WORKERS = 16_000, 11
HERE_GAMES, HERE_WORKERS = 4_000, 4
TAG = "e84-late"


def say(text):
    print(time.strftime("[%b %d %H:%M:%S] ") + text, flush=True)


def selfplay_cmd(py, net, starts, out, games, first, workers):
    cmd = [py, "-m", "catanatron.colonist.selfplay", net, out]
    cmd += ["--games", str(games), "--seed", str(first), "--workers", str(workers)]
    cmd += ["--simulations", "400", "--full-rate", "0.25", "--fast-simulations", "24"]
    cmd += ["--human-starts", starts, "--human-start-rate", "1.0"]
    return cmd


def wait_for_e85():
    said = False
    while True:
        done = os.path.exists(os.path.join(ROOT, "data/orchestrator/e85/net.npz"))
        held = ssh(PC, f"cat {LOCK} 2>/dev/null", False).strip()
        if done and held != "e85-value":
            say("E85 done; starting")
            return
        if not said:
            say(f"waiting for E85: net {done}, lock {held!r}")
            said = True
        time.sleep(60)


def play():
    os.makedirs(DATA, exist_ok=True)
    have = [f for f in os.listdir(DATA) if f.endswith(".npz")]
    if have:
        say(f"{len(have)} shards already in {DATA}; skipping play")
        return
    ssh(PC, f"rm -rf {WORK}; mkdir -p {WORK}/data")
    rsync(os.path.join(ROOT, R189), f"{PC}:{WORK}/init.npz")
    rsync(os.path.join(ROOT, STARTS), f"{PC}:{WORK}/starts.jsonl.gz")
    remote = selfplay_cmd(
        RPY,
        f"{WORK}/init.npz",
        f"{WORK}/starts.jsonl.gz",
        f"{WORK}/data",
        PC_GAMES,
        SEED,
        PC_WORKERS,
    )
    # Detached on the PC, so a dropped ssh link doesn't kill the games.
    inner = f"cd {ROOT}; nice -n 5 " + " ".join(map(shlex.quote, remote))
    inner += f" > {WORK}/play.log 2>&1; echo $? > {WORK}/play.rc"
    script = "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1; "
    script += (
        f"nohup setsid bash -c {shlex.quote(inner)} > /dev/null 2>&1 < /dev/null &"
    )
    t0 = time.time()
    say(f"playing {PC_GAMES} on the PC and {HERE_GAMES} here")
    ssh(PC, script)
    here_dir = os.path.join(OUT, "data-here")
    local = selfplay_cmd(
        ".venv/bin/python",
        R189,
        STARTS,
        here_dir,
        HERE_GAMES,
        SEED + PC_GAMES,
        HERE_WORKERS,
    )
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    here = subprocess.run(
        local,
        cwd=ROOT,
        env=env,
        preexec_fn=lambda: os.nice(19),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    say(f"here: done in {time.time() - t0:.0f} s, exit {here.returncode}")
    say("here: " + " | ".join(here.stdout.strip().splitlines()[-2:]))
    while True:
        rc = ssh(PC, f"cat {WORK}/play.rc 2>/dev/null", False).strip()
        if rc:
            break
        time.sleep(60)
    tail = ssh(PC, f"tail -2 {WORK}/play.log", False).strip().replace("\n", " | ")
    say(f"PC: done in {time.time() - t0:.0f} s, exit {rc}: {tail[-300:]}")
    if here.returncode or rc != "0":
        raise SystemExit("self-play failed")
    rsync(f"{PC}:{WORK}/data/", DATA + "/", "--include=shard_*.npz", "--exclude=*")
    for f in os.listdir(here_dir):
        if f.startswith("shard_") and f.endswith(".npz"):
            os.rename(os.path.join(here_dir, f), os.path.join(DATA, f))
    say(f"{len(os.listdir(DATA))} files in {DATA}")


def train():
    say("copying training data to the PC")
    ssh(PC, f"rm -rf {WORK}/train; mkdir -p {WORK}/train/human {WORK}/train/late")
    rsync(os.path.join(ROOT, R189), f"{PC}:{WORK}/init.npz")
    rsync(
        DATA + "/", f"{PC}:{WORK}/train/late/", "--include=shard_*.npz", "--exclude=*"
    )
    rsync(
        os.path.join(ROOT, "data/human/train/value") + "/", f"{PC}:{WORK}/train/human/"
    )
    folders = []
    for r in ROUNDS:
        source = os.path.join(ROOT, f"runs/g1/round_{r:03d}/data") + "/"
        rsync(
            source, f"{PC}:{WORK}/train/r{r}/", "--include=shard_*.npz", "--exclude=*"
        )
        folders.append(f"{WORK}/train/r{r}")
    folders += [f"{WORK}/train/human"] * 12 + [f"{WORK}/train/late"]
    cmd = [RPY, "-m", "catanatron.colonist.train", *folders]
    cmd += ["--out", f"{WORK}/out", "--init", f"{WORK}/init.npz"]
    cmd += [
        "--epochs",
        "2",
        "--lr",
        "3e-4",
        "--ema",
        "0.9997",
        "--max-positions",
        "4000000",
    ]
    cmd += ["--device", "cuda", "--seed", "84"]
    say("training: " + " ".join(cmd[3 + len(folders) :]))
    out = ssh(PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1", False)
    with open(os.path.join(OUT, "train.txt"), "w") as f:
        f.write(out)
    print(out[-3000:], flush=True)
    for name in ("net.npz", "last.npz", "log.csv"):
        rsync(f"{PC}:{WORK}/out/{name}", os.path.join(OUT, name))


def main():
    wait_for_e85()
    lock(TAG)
    try:
        say("gpu.lock held")
        play()
        train()
        ssh(PC, f"rm -rf {WORK}")
    finally:
        unlock(TAG)
        say("gpu.lock released")
    say("done")


if __name__ == "__main__":
    main()
