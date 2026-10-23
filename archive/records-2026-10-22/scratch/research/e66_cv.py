"""E66: does a people model tuned to 2300+ players predict them better?

4-fold cross-check by game over every 2300+ opponent game we hold (train and
holdout splits; ratings from e66_count.py). For fold k:
  control k = opponent_model train (production's recipe) from runs/g1/best.npz
              on the train split minus fold k's 2300+ games, plus the holdout
              split's 2300+ games outside fold k;
  tuned k   = control k fine-tuned on the 2300+ games outside fold k only
              (Maia-style), lr 1e-4.
Both are scored per game on fold k's 2300+ moves and on the holdout's other
moves (under 2300 or unrated). Training on the PC's GPU under gpu.lock.

    .venv/bin/python data/orchestrator/scratch/research/e66_cv.py [--folds 4] [--score-only]

Results: data/orchestrator/e66/cv.txt (log) and cv.json (per-game rows).
"""

import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone

import numpy as np

ROOT = "/home/owen/colonist-ai"
HERE = f"{ROOT}/data/orchestrator/e66"
KIND = "opponent_exact"
KEY = os.path.expanduser("~/.ssh/colonist_worker_ed25519")
SSH = f"ssh -i {KEY} -o BatchMode=yes -o ConnectTimeout=20"
PC = "owen@100.125.136.114"
REMOTE = "/home/owen/e66"
RPY = "/home/owen/colonist-ai/.venv/bin/python"
LOCK = "/home/owen/colonist-ai/gpu.lock"
TORONTO = timezone(timedelta(hours=-4))
BAR = 2300


def log(line):
    stamp = datetime.now(TORONTO).strftime("%b %d, %-I:%M %p")
    print(f"[{stamp}] {line}", flush=True)
    with open(f"{HERE}/cv.txt", "a") as out:
        out.write(f"[{stamp}] {line}\n")


def ssh(command, check=True):
    r = subprocess.run(SSH.split() + [PC, command], stdout=subprocess.PIPE, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{command[:160]} -> {r.returncode}\n{r.stdout[-2000:]}")
    return r.stdout


def rsync(source, destination, *extra):
    subprocess.run(["rsync", "-a", *extra, "-e", SSH, source, destination], check=True)


def lock(tag):
    while True:
        r = subprocess.run(
            SSH.split() + [PC, f"set -o noclobber; echo {tag} > {LOCK}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if r.returncode == 0 or ssh(f"cat {LOCK}", False).strip() == tag:
            return
        time.sleep(60)


def unlock(tag):
    ssh(f"grep -qx {tag} {LOCK} && rm -f {LOCK}", False)


def gid(path):
    return os.path.basename(path).split("_")[1]


def build(folds):
    ratings = json.load(open(f"{HERE}/ratings.json"))
    mmr = lambda p: (ratings.get(gid(p)) or {}).get("mmr")
    strong = lambda p: mmr(p) is not None and mmr(p) >= BAR
    train = sorted(glob.glob(f"{ROOT}/data/human/train/{KIND}/shard_*.npz"))
    hold = sorted(glob.glob(f"{ROOT}/data/human/holdout/{KIND}/shard_*.npz"))
    top = [p for p in train + hold if strong(p)]
    rest = [p for p in hold if not strong(p)]
    fold = lambda p: int(gid(p)) % folds
    plan = {"rest": rest, "folds": []}
    for k in range(folds):
        d = f"{HERE}/folds/{k}"
        shutil.rmtree(d, ignore_errors=True)
        for sub in ("train", "tune"):
            os.makedirs(f"{d}/{sub}")
        held = [p for p in top if fold(p) == k]
        tune = [p for p in top if fold(p) != k]
        base = [p for p in train if not strong(p)] + tune
        for sub, paths in (("train", base), ("tune", tune)):
            for p in paths:
                os.symlink(p, f"{d}/{sub}/{os.path.basename(p)}")
        plan["folds"].append({"eval": held, "train": len(base), "tune": len(tune)})
        log(
            f"fold {k}: {len(base)} train games, {len(tune)} tune, {len(held)} eval (2300+)"
        )
    log(f"rest: {len(rest)} holdout games under 2300 or unrated")
    return plan


def train_fold(k, args):
    tag = f"e66-fold-{k}"
    lock(tag)
    try:
        w = f"{REMOTE}/{k}"
        ssh(f"rm -rf {w}; mkdir -p {w}")
        rsync(f"{ROOT}/runs/g1/best.npz", f"{PC}:{REMOTE}/init.npz", "-L")
        for sub in ("train", "tune"):
            rsync(f"{HERE}/folds/{k}/{sub}/", f"{PC}:{w}/{sub}/", "-L")
        om = [RPY, "-m", "catanatron.colonist.opponent_model", "train"]
        steps = (
            ("control", f"{w}/train", f"{REMOTE}/init.npz", ["--epochs", "10"]),
            (
                "tuned",
                f"{w}/tune",
                f"{w}/control/net.npz",
                ["--epochs", "10", "--lr", str(args.tune_lr)],
            ),
        )
        for name, data, init, extra in steps:
            cmd = om + [data, "--init", init, "--out", f"{w}/{name}", "--"]
            cmd += ["--device", "cuda", "--seed", str(k)] + extra
            out = ssh(f"cd {ROOT}; " + " ".join(map(shlex.quote, cmd)) + " 2>&1")
            ep = [l for l in out.splitlines() if l.startswith("epoch")]
            log(f"fold {k} {name}: {ep[-1] if ep else out.strip().splitlines()[-1:]}")
            os.makedirs(f"{HERE}/nets/{k}", exist_ok=True)
            rsync(f"{PC}:{w}/{name}/net.npz", f"{HERE}/nets/{k}/{name}.npz")
        ssh(f"rm -rf {w}")
    finally:
        unlock(tag)


def per_game(paths, nets):
    from catanatron.colonist.opponent_model import score

    rows = []
    for p in paths:
        s = score([p], nets)
        if s.get("uniform", {}).get("rows"):
            rows.append(
                {
                    "game": gid(p),
                    "n": s["uniform"]["rows"],
                    **{
                        f"{k}_hit": round(v["accuracy"] * v["rows"])
                        for k, v in s.items()
                    },
                    **{f"{k}_ce": v["cross_entropy"] * v["rows"] for k, v in s.items()},
                }
            )
    return rows


def summary(rows, a, b, label, boot=2000):
    n = np.array([r["n"] for r in rows], float)
    ha, hb = (np.array([r[f"{x}_hit"] for r in rows], float) for x in (a, b))
    ca, cb = (np.array([r[f"{x}_ce"] for r in rows], float) for x in (a, b))
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(n), (boot, len(n)))
    dacc = (hb[idx].sum(1) - ha[idx].sum(1)) / n[idx].sum(1)
    dce = (cb[idx].sum(1) - ca[idx].sum(1)) / n[idx].sum(1)
    log(
        f"{label}: {int(n.sum())} moves, {len(n)} games | top-1 {a} {ha.sum() / n.sum():.2%}"
        f" {b} {hb.sum() / n.sum():.2%} (diff {(hb.sum() - ha.sum()) / n.sum():+.2%},"
        f" 95% {np.percentile(dacc, 2.5):+.2%} to {np.percentile(dacc, 97.5):+.2%}) |"
        f" CE {a} {ca.sum() / n.sum():.4f} {b} {cb.sum() / n.sum():.4f}"
        f" (diff {(cb.sum() - ca.sum()) / n.sum():+.4f}, 95% {np.percentile(dce, 2.5):+.4f}"
        f" to {np.percentile(dce, 97.5):+.4f})"
    )


def score_all(plan, folds):
    from catanatron.colonist.net import Net

    top, rest = [], []
    for k in range(folds):
        nets = {x: Net.load(f"{HERE}/nets/{k}/{x}.npz") for x in ("control", "tuned")}
        t = per_game(plan["folds"][k]["eval"], nets)
        r = per_game(plan["rest"], nets)
        summary(t, "control", "tuned", f"fold {k} 2300+")
        summary(r, "control", "tuned", f"fold {k} rest")
        top += t
        rest += r
    summary(top, "control", "tuned", "ALL FOLDS 2300+")
    summary(rest, "control", "tuned", "ALL FOLDS rest (4 copies of the same holdout)")
    json.dump({"top": top, "rest": rest}, open(f"{HERE}/cv.json", "w"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--tune-lr", type=float, default=1e-4)
    p.add_argument("--score-only", action="store_true")
    args = p.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    plan = build(args.folds)
    if not args.score_only:
        for k in range(args.folds):
            train_fold(k, args)
    score_all(plan, args.folds)
    log("done")


if __name__ == "__main__":
    main()
