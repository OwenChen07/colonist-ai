"""E47: weight averages of the rounds trained from round 73.

    .venv/bin/python data/orchestrator/scratch/research/e47_average.py

A = mean of rounds 108-110; B = mean of rounds 97-110 that scored 49%+ in
their gate (log.csv). Float arrays are averaged; every other array must be
identical across the nets. Writes data/orchestrator/e47/{A,B}/net.npz and
checks each loads and evaluates like its parts.
"""

import csv
import os

import numpy as np

ROOT = "/home/owen/colonist-ai"
NETS = f"{ROOT}/runs/g1/nets"
OUT = f"{ROOT}/data/orchestrator/e47"


def path(n):
    return f"{NETS}/round_{n:03d}.npz"


def average(rounds, dest):
    zs = [np.load(path(n), allow_pickle=False) for n in rounds]
    keys = zs[0].files
    for n, z in zip(rounds, zs):
        assert z.files == keys, f"round {n} keys differ"
    out = {}
    averaged = 0
    for k in keys:
        arrays = [z[k] for z in zs]
        shapes = {a.shape for a in arrays}
        assert len(shapes) == 1, f"{k}: shapes {shapes}"
        if arrays[0].dtype.kind == "f":
            out[k] = np.mean(np.stack([a.astype(np.float64) for a in arrays]), 0)
            out[k] = out[k].astype(arrays[0].dtype)
            averaged += 1
        else:
            for n, a in zip(rounds, arrays):
                assert np.array_equal(a, arrays[0]), f"{k} differs in round {n}"
            out[k] = arrays[0]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    np.savez(dest, **out)
    print(f"{dest}: {len(rounds)} nets {rounds}, {averaged} float arrays averaged")
    return keys


def distance(a, b, keys):
    za, zb = np.load(a), np.load(b)
    num = sum(
        float(((za[k] - zb[k]) ** 2).sum()) for k in keys if za[k].dtype.kind == "f"
    )
    return num**0.5


def main():
    scores = {}
    with open(f"{ROOT}/runs/g1/log.csv") as f:
        for row in csv.reader(f):
            if row and row[0].isdigit():
                scores[int(row[0])] = float(row[3])
    a = [108, 109, 110]
    b = [n for n in range(97, 111) if scores.get(n, 0) >= 0.49]
    keys = average(a, f"{OUT}/A/net.npz")
    average(b, f"{OUT}/B/net.npz")
    r73 = path(73)
    for name in [*(path(n) for n in b), f"{OUT}/A/net.npz", f"{OUT}/B/net.npz"]:
        print(
            f"  |{os.path.basename(os.path.dirname(name)) if 'e47' in name else os.path.basename(name)} - r73| = {distance(name, r73, keys):.3f}"
        )
    print(f"  |r108 - r109| = {distance(path(108), path(109), keys):.3f}")
    # Load and evaluate on the live holdout as a sanity check (and the
    # guardrail's number): live MSE by phase.
    from catanatron.colonist.calibrate import calibrate, describe, load_value_shards
    from catanatron.colonist.net import Net

    data = load_value_shards([f"{ROOT}/data/human/holdout/value"])
    for name, p in [
        ("round 73", r73),
        ("round 110", path(110)),
        ("A", f"{OUT}/A/net.npz"),
        ("B", f"{OUT}/B/net.npz"),
    ]:
        print(f"{name}: {describe(calibrate(Net.load(p), data))}", flush=True)


if __name__ == "__main__":
    main()
