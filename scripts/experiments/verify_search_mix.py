"""Verify paired seed coverage and human-start assignments for search mix."""

import argparse
import json
import re
from pathlib import Path

import numpy as np


SHARD = re.compile(r"shard_(\d+)_(\d+)\.npz")


def read_branch(path):
    by_seed = {}
    full = positions = setup = 0
    files = sorted(Path(path).glob("shard_*.npz"))
    for file in files:
        match = SHARD.fullmatch(file.name)
        if match is None:
            raise ValueError(f"unexpected shard filename: {file.name}")
        start, count = map(int, match.groups())
        with np.load(file) as shard:
            seeds = shard["seed"].tolist()
            human = shard["human_start"].tolist()
            if seeds != list(range(start, start + count)):
                raise ValueError(f"incorrect seeds in {file.name}")
            for seed, assigned in zip(seeds, human):
                if seed in by_seed:
                    raise ValueError(f"duplicate seed {seed}")
                by_seed[seed] = bool(assigned)
            positions += len(shard["full"])
            full += int(shard["full"].sum())
            setup += int((shard["turn"] == 0).sum())
    return by_seed, {
        "shards": len(files),
        "positions": positions,
        "full": full,
        "setup": setup,
    }


def verify(control, test, first=4043000000, games=3500):
    a, control_stats = read_branch(control)
    b, test_stats = read_branch(test)
    expected = set(range(first, first + games))
    if set(a) != expected or set(b) != expected:
        raise ValueError("control or test has missing or extra seeds")
    if a != b:
        raise ValueError("human-start assignments differ between branches")
    if test_stats["full"] <= control_stats["full"]:
        raise ValueError("test did not create more full-search policy targets")
    return {
        "status": "verified",
        "seed_first": first,
        "seed_last": first + games - 1,
        "games_per_branch": games,
        "human_assignments": sum(a.values()),
        "control": control_stats,
        "test": test_stats,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control")
    parser.add_argument("test")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = verify(args.control, args.test)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
