"""E54 step 3: the actor-learner loop (offline; nothing in production changes).

    .venv/bin/python data/orchestrator/scratch/research/e54_loop.py OUT \
        [--iters 2] [--games 2000] [--workers 4] [--device cpu] [--lr 3e-5]
        [--start runs/g1/nets/round_073.npz] [--eval-every 10] [--eval-games 1000]

One iteration: --workers pg.py processes play --games games in all (the
learner against itself half the time, else round 73, 66, 13 or the people
model), then 1 epoch of train.py --pg from the current learner, KL to round
73. The new net is OUT/iter-N/net.npz. The previous iteration's shards are
deleted once the next net exists. Every --eval-every iterations (and at the
end): raw vs raw against round 73 (Z:net:0, sampled from the prior),
--eval-games games on --workers cores, appended to OUT/eval.txt.
Restartable: an iteration whose net.npz exists is skipped.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = "/home/owen/colonist-ai"
PY = f"{ROOT}/.venv/bin/python"
TORONTO = timezone(timedelta(hours=-4))  # EDT until Nov 1
R73 = f"{ROOT}/runs/g1/nets/round_073.npz"
POOL = [R73, f"{ROOT}/runs/g1/nets/round_066.npz", f"{ROOT}/runs/g1/nets/round_013.npz"]
HUMAN = f"{ROOT}/models/human/net.npz"


def log(out, line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(os.path.join(out, "loop.txt"), "a") as f:
        f.write(f"[{stamp}] {line}\n")


def run(cmd, env):
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, text=True)


def play(net, data, first, games, workers, env, self_rate):
    per = [games // workers + (i < games % workers) for i in range(workers)]
    procs, start = [], first
    for n in per:
        cmd = [PY, "-m", "catanatron.colonist.pg", net, data, "--games", str(n)]
        cmd += ["--first", str(start), "--pool", *POOL, "--human", HUMAN]
        cmd += ["--self-rate", str(self_rate)]
        procs.append(run(cmd, env))
        start += n
    lines = []
    for p in procs:
        lines.append(p.communicate()[0].strip())
        if p.returncode:
            sys.exit(f"pg.py failed: {lines[-1]}")
    return lines


def evaluate(net, games, workers, seed, env):
    cmd = [PY, "-m", "catanatron.colonist.evaluate", f"Z:{net}:0", f"Z:{R73}:0"]
    cmd += ["--games", str(games), "--workers", str(workers), "--seed", str(seed)]
    out = run(cmd, env).communicate()[0]
    return out.strip().splitlines()[-1] if out.strip() else "(no output)"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("--iters", type=int, default=2)
    p.add_argument("--games", type=int, default=2000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--device", default="cpu")
    p.add_argument("--lr", type=float, default=3e-5)
    p.add_argument("--pg-kl", type=float, default=0.02)
    p.add_argument("--self-rate", type=float, default=0.5)
    p.add_argument("--start", default=R73)
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--eval-games", type=int, default=1000)
    p.add_argument("--seed", type=int, default=5_400_000_000)
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    train_env = dict(env, OMP_NUM_THREADS=str(args.workers))
    net = args.start
    for it in range(1, args.iters + 1):
        folder = os.path.join(args.out, f"iter-{it}")
        new = os.path.join(folder, "net.npz")
        if not os.path.exists(new):
            data = os.path.join(folder, "data")
            shutil.rmtree(data, ignore_errors=True)
            t0 = time.time()
            first = args.seed + it * 1_000_000
            lines = play(
                net, data, first, args.games, args.workers, env, args.self_rate
            )
            t1 = time.time()
            log(args.out, f"iter {it}: played {args.games} games in {t1 - t0:.0f} s")
            for line in lines:
                log(args.out, f"  {line}")
            cmd = [PY, "-m", "catanatron.colonist.train", data, "--out", folder]
            cmd += ["--init", net, "--pg", "--pg-prior", R73, "--epochs", "1"]
            cmd += ["--lr", str(args.lr), "--pg-kl", str(args.pg_kl)]
            cmd += ["--device", args.device, "--seed", str(it)]
            out = run(cmd, train_env).communicate()[0]
            tail = [l for l in out.splitlines() if l.startswith("epoch")]
            if not os.path.exists(new):
                sys.exit(f"train.py made no net:\n{out[-2000:]}")
            log(args.out, f"iter {it}: trained in {time.time() - t1:.0f} s")
            for line in tail:
                log(args.out, f"  {line}")
        prev = os.path.join(args.out, f"iter-{it - 1}", "data")
        shutil.rmtree(prev, ignore_errors=True)
        net = new
        if it % args.eval_every == 0 or it == args.iters:
            seed = args.seed + 900_000_000
            line = evaluate(net, args.eval_games, args.workers, seed, env)
            log(args.out, f"iter {it}: raw vs raw r73, {args.eval_games}: {line}")
            with open(os.path.join(args.out, "eval.txt"), "a") as f:
                f.write(f"{it}\t{line}\n")
    shutil.rmtree(os.path.join(args.out, f"iter-{args.iters}", "data"), True)


if __name__ == "__main__":
    main()
