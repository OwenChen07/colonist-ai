"""E54 step 4: e54_loop.py's actor-learner loop across machines (offline).

    .venv/bin/python data/orchestrator/scratch/research/e54_dist.py OUT \
        [--iters 30] [--games 20000] [--eval-every 10] [--eval-games 1000]

Games (pg.py, 4 processes a host) on the self-play hosts lent for E54, the
learner (train.py --pg, 1 epoch) on the PC's GPU under gpu.lock, raw vs raw
evaluation against round 73 on the first host. Waits until training has
taken the hosts out of workers.env and their last fleet jobs have ended.
Same seeds, pool and settings as e54_loop.py; restartable the same way.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e54_loop import HUMAN, POOL, R73, ROOT, log  # noqa: E402

HOSTS = ["owen@10.128.0.8", "owen@10.128.0.9"]
PC = "owen@100.125.136.114"
KEY = os.path.expanduser("~/.ssh/colonist_worker_ed25519")
SSH = f"ssh -i {KEY} -o BatchMode=yes -o ConnectTimeout=20"
REMOTE = "/home/owen/e54"
RPY = "/home/owen/colonist-ai/.venv/bin/python"
LOCK = "/home/owen/colonist-ai/gpu.lock"
PER_HOST = 4
# e60_dist.py reuses this driver with its own name, folder, pool and self-rate.
NAME = "e54"
SELF_RATE = 0.5


def ssh(host, command, check=True):
    r = subprocess.run(SSH.split() + [host, command], stdout=subprocess.PIPE, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{host}: {command[:120]} -> {r.returncode}")
    return r.stdout


def rsync(source, destination, *extra):
    subprocess.run(["rsync", "-a", *extra, "-e", SSH, source, destination], check=True)


def retry(what, fn, tries=5):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network blips, a spot VM restarting
            print(f"{what} failed ({e}); retry {i + 1}", flush=True)
            # starts any stopped VM labelled autostart=yes (efficiency, Oct 22)
            subprocess.run([os.path.join(ROOT, "scripts/vm/gcp_start_spot.sh")])
            time.sleep(60)
    sys.exit(f"{what} failed {tries} times")


def wait_for_hosts(out, after_dir=None):
    env = os.path.join(ROOT, "scripts/vm/workers.env")
    said = False
    if after_dir:  # training's ask: start once this round's self-play has begun
        log(out, f"waiting for {after_dir}")
        while not os.path.isdir(os.path.join(ROOT, after_dir)):
            time.sleep(60)
    while True:
        text = open(env).read()
        listed = [h for h in HOSTS if h in text]
        busy = [
            h
            for h in HOSTS
            if not listed
            and ssh(h, "pgrep -fc '[c]atanatron[.]colonist' || true", False).strip()
            != "0"
        ]
        if not listed and not busy:
            log(out, "hosts free: out of workers.env and idle")
            return
        if not said:
            log(out, f"waiting: listed {listed}, busy {busy}")
            said = True
        time.sleep(120)


def setup(pool_dir=None):
    """pool_dir: local copies named as on the hosts (E83: run 1's frozen pool,
    whose people model is Oct 19's, not the hourly re-fit one)."""
    for h in dict.fromkeys(HOSTS + [PC]):
        ssh(h, f"mkdir -p {REMOTE}/pool")
        for path in POOL + ([HUMAN] if HUMAN else []):
            name = path.replace("/", "_")
            source = os.path.join(pool_dir, name) if pool_dir else path
            rsync(source, f"{h}:{REMOTE}/pool/{name}")


def remote(path):
    return f"{REMOTE}/pool/{path.replace('/', '_')}"


def play(net, data, first, games):
    """pg.py on every host; the shards end up in data (local)."""
    for h in HOSTS:
        rsync(net, f"{h}:{REMOTE}/net.npz")
        ssh(h, f"rm -rf {REMOTE}/data")
    workers = len(HOSTS) * PER_HOST
    per = [games // workers + (i < games % workers) for i in range(workers)]
    procs, start = [], first
    for i, h in enumerate(HOSTS):
        cmds = []
        for n in per[i * PER_HOST : (i + 1) * PER_HOST]:
            cmd = [RPY, "-m", "catanatron.colonist.pg", f"{REMOTE}/net.npz"]
            cmd += [f"{REMOTE}/data", "--games", str(n), "--first", str(start)]
            cmd += ["--pool", *map(remote, POOL)]
            cmd += ["--human", remote(HUMAN)] if HUMAN else []
            cmd += ["--self-rate", str(SELF_RATE)]
            cmds.append(" ".join(map(shlex.quote, cmd)))
            start += n
        script = "export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1; "
        script += f"cd {ROOT}; " + " & ".join(cmds) + " & wait"
        procs.append(
            subprocess.Popen(
                SSH.split() + [h, script], stdout=subprocess.PIPE, text=True
            )
        )
    lines = []
    for p in procs:
        lines += p.communicate()[0].strip().splitlines()
        if p.returncode:
            raise RuntimeError(f"pg.py failed: {lines[-1:]}")
    os.makedirs(data, exist_ok=True)
    for h in HOSTS:
        rsync(f"{h}:{REMOTE}/data/", data + "/")
    shards = [f for f in os.listdir(data) if f.endswith(".npz")]
    if len(shards) != workers:
        raise RuntimeError(f"{len(shards)} shards, expected {workers}")
    return lines


def lock(tag):
    while True:
        r = subprocess.run(
            SSH.split() + [PC, f"set -o noclobber; echo {tag} > {LOCK}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if r.returncode == 0 or ssh(PC, f"cat {LOCK}", False).strip() == tag:
            return
        time.sleep(60)


def unlock(tag):
    ssh(PC, f"grep -qx {tag} {LOCK} && rm -f {LOCK}", False)


def match_age():
    """Seconds since the loop's current promotion match began, or None when
    the loop isn't in its match step (train.log times are UTC)."""
    import json

    step = json.load(open(os.path.join(ROOT, "runs/g1/state.json")))["step"]
    if step != "match":
        return None
    t = None
    for line in open(os.path.join(ROOT, "runs/train.log")):
        if "promotion match (" in line:
            t = line[1:9]
    if t is None:
        return None
    h, m, s = map(int, t.split(":"))
    now = time.gmtime()
    return (
        now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec - h * 3600 - m * 60 - s
    ) % 86400


def wait_for_window(out, it):
    """VM mode: start an iteration only in the first 2 min of a promotion
    match, when the GPU VM is idle until the next round's training (which
    waits up to 20 min on gpu.lock), as E53's legs did."""
    said = False
    while True:
        age = match_age()
        if age is not None and age < 120:
            log(out, f"iter {it}: promotion match {age} s old; starting")
            return
        if not said:
            log(out, f"iter {it}: waiting for a promotion match")
            said = True
        time.sleep(20)


def train(net, data, folder, it, args, held=False):
    """train.py --pg on the GPU host; the new net ends up in folder."""
    tag = f"{NAME}-iter-{it}"
    if not held:
        lock(tag)
    try:
        work = f"{REMOTE}/iter-{it}"
        ssh(PC, f"rm -rf {REMOTE}/iter-*; mkdir -p {work}")
        rsync(net, f"{PC}:{REMOTE}/init.npz")
        rsync(data + "/", f"{PC}:{work}/data/")
        cmd = [RPY, "-m", "catanatron.colonist.train", f"{work}/data"]
        cmd += ["--out", work, "--init", f"{REMOTE}/init.npz", "--pg"]
        cmd += ["--pg-prior", remote(R73), "--epochs", "1"]
        cmd += ["--lr", str(args.lr), "--pg-kl", str(args.pg_kl)]
        cmd += ["--device", "cuda", "--seed", str(it)]
        out = ssh(PC, f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1")
        rsync(f"{PC}:{work}/net.npz", os.path.join(folder, "net.npz"))
        ssh(PC, f"rm -rf {work}")
    finally:
        if not held:
            unlock(tag)
    return [l for l in out.splitlines() if l.startswith("epoch")]


def evaluate(net, games, seed):
    h = HOSTS[0]
    rsync(net, f"{h}:{REMOTE}/eval.npz")
    cmd = [RPY, "-m", "catanatron.colonist.evaluate", f"Z:{REMOTE}/eval.npz:0"]
    cmd += [f"Z:{remote(R73)}:0", "--games", str(games)]
    cmd += ["--workers", str(PER_HOST), "--seed", str(seed)]
    out = ssh(h, f"cd {ROOT}; OMP_NUM_THREADS=1 " + " ".join(map(shlex.quote, cmd)))
    return out.strip().splitlines()[-1] if out.strip() else "(no output)"


def main():
    global HOSTS, PER_HOST, PC
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("--iters", type=int, default=30)
    p.add_argument("--games", type=int, default=20000)
    p.add_argument("--lr", type=float, default=3e-5)
    p.add_argument("--pg-kl", type=float, default=0.02)
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--eval-games", type=int, default=1000)
    p.add_argument("--seed", type=int, default=5_400_000_000)
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--after-dir", help="wait until ROOT/this exists first")
    p.add_argument("--hosts", help="comma-separated, for a smoke test")
    p.add_argument("--per-host", type=int, default=PER_HOST)
    p.add_argument(
        "--vm",
        help="play, learn and evaluate all on this GPU host, one iteration per "
        "promotion match, holding its gpu.lock (owner, Oct 19)",
    )
    p.add_argument(
        "--pool-dir", help="frozen pool copies to send instead of the live files"
    )
    args = p.parse_args()
    HOSTS = args.hosts.split(",") if args.hosts else HOSTS
    PER_HOST = args.per_host
    if args.vm:
        HOSTS, PC = [args.vm], args.vm

    os.makedirs(args.out, exist_ok=True)
    if not args.no_wait and not args.vm:
        wait_for_hosts(args.out, args.after_dir)
    retry("setup", lambda: setup(args.pool_dir))
    net = R73
    for it in range(1, args.iters + 1):
        folder = os.path.join(args.out, f"iter-{it}")
        new = os.path.join(folder, "net.npz")
        fresh = not os.path.exists(new)
        tag = f"{NAME}-iter-{it}"
        if args.vm and fresh:
            wait_for_window(args.out, it)
            lock(tag)
        try:
            if fresh:
                data = os.path.join(folder, "data")
                shutil.rmtree(data, ignore_errors=True)
                t0 = time.time()
                first = args.seed + it * 1_000_000
                lines = retry(
                    f"iter {it} play", lambda: play(net, data, first, args.games)
                )
                t1 = time.time()
                log(
                    args.out, f"iter {it}: played {args.games} games in {t1 - t0:.0f} s"
                )
                for line in lines:
                    log(args.out, f"  {line}")
                held = bool(args.vm)
                tail = retry(
                    f"iter {it} train", lambda: train(net, data, folder, it, args, held)
                )
                log(args.out, f"iter {it}: trained in {time.time() - t1:.0f} s")
                for line in tail:
                    log(args.out, f"  {line}")
                shutil.rmtree(data, ignore_errors=True)
            net = new
            # A resumed run doesn't re-score iterations it finished before.
            if (it % args.eval_every == 0 or it == args.iters) and fresh:
                seed = args.seed + 900_000_000
                line = retry("eval", lambda: evaluate(net, args.eval_games, seed))
                log(args.out, f"iter {it}: raw vs raw r73, {args.eval_games}: {line}")
                with open(os.path.join(args.out, "eval.txt"), "a") as f:
                    f.write(f"{it}\t{line}\n")
        finally:
            if args.vm and fresh:
                unlock(tag)
    log(args.out, "done")


if __name__ == "__main__":
    main()
