"""Verify a paired tactical-start self-play branch before it can be trained."""

import argparse
import json
import random
from pathlib import Path

import numpy as np

from catanatron.colonist.starts import load


def chosen_cut(seed, starts, rate):
    rng = random.Random(seed ^ 0x57A27)
    if rng.random() >= rate:
        return None
    entry = starts[rng.randrange(len(starts))]
    cut_rng = random.Random(seed)
    return entry["cuts"][cut_rng.randrange(len(entry["cuts"]))]


def verify(
    control_dir,
    target_dir,
    original_starts,
    target_starts,
    first,
    games,
    rate,
    shard_size=16,
):
    original = load(original_starts)
    target = load(target_starts)
    if len(original) != len(target):
        raise ValueError("starts entry count changed")
    for i, (a, b) in enumerate(zip(original, target)):
        if (
            {k: v for k, v in a.items() if k != "cuts"}
            != {k: v for k, v in b.items() if k not in ("cuts", "source_cut_count")}
            or not set(b["cuts"]) <= set(a["cuts"])
            or (
                b.get("source_cut_count")
                != (len(a["cuts"]) if b["cuts"] != a["cuts"] else None)
            )
        ):
            raise ValueError(f"start entry {i} changed outside its allowed cuts")
    control_files = sorted(Path(control_dir).glob("shard_*.npz"))
    target_files = sorted(Path(target_dir).glob("shard_*.npz"))
    expected = [
        f"shard_{seed}_{min(shard_size, first + games - seed)}.npz"
        for seed in range(first, first + games, shard_size)
    ]
    if sorted(p.name for p in control_files) != sorted(expected):
        raise ValueError("control shards missing or extra")
    if sorted(p.name for p in target_files) != sorted(expected):
        raise ValueError("target shards missing or extra")
    counts = {
        "status": "verified",
        "games": games,
        "shards": len(expected),
        "seed_first": first,
        "seed_last": first + games - 1,
        "changed_cuts": 0,
        "same_cut_games": 0,
        "same_cut_action_sequences_equal": 0,
        "same_cut_outcomes_equal": 0,
        "human_assignments": 0,
    }
    for name in expected:
        with (
            np.load(Path(control_dir) / name) as a,
            np.load(Path(target_dir) / name) as b,
        ):
            seed_a, seed_b = a["seed"], b["seed"]
            first_seed = int(name.split("_")[1])
            wanted = np.arange(first_seed, first_seed + len(seed_a))
            if not np.array_equal(seed_a, wanted) or not np.array_equal(seed_a, seed_b):
                raise ValueError(f"seed coverage mismatch in {name}")
            if not np.array_equal(a["human_start"], b["human_start"]):
                raise ValueError(f"human-start assignment mismatch in {name}")
            for j, seed in enumerate(seed_a):
                seed = int(seed)
                cut_a = chosen_cut(seed, original, rate)
                cut_b = chosen_cut(seed, target, rate)
                if bool(a["human_start"][j]) != (cut_a is not None):
                    raise ValueError(f"human-start seed mismatch for {seed}")
                counts["human_assignments"] += cut_a is not None
                if cut_a != cut_b:
                    counts["changed_cuts"] += 1
                    continue
                counts["same_cut_games"] += 1
                rows_a = a["game"] == j
                rows_b = b["game"] == j
                actions_equal = np.array_equal(a["action"][rows_a], b["action"][rows_b])
                outcomes_equal = np.array_equal(a["z"][rows_a], b["z"][rows_b])
                counts["same_cut_action_sequences_equal"] += actions_equal
                counts["same_cut_outcomes_equal"] += outcomes_equal
                if not actions_equal or not outcomes_equal:
                    raise ValueError(f"unchanged start diverged for seed {seed}")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("control_dir")
    parser.add_argument("target_dir")
    parser.add_argument("original_starts")
    parser.add_argument("target_starts")
    parser.add_argument("--first", type=int, default=4043000000)
    parser.add_argument("--games", type=int, default=3500)
    parser.add_argument("--rate", type=float, default=0.35)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    counts = verify(
        args.control_dir,
        args.target_dir,
        args.original_starts,
        args.target_starts,
        args.first,
        args.games,
        args.rate,
    )
    content = json.dumps(counts, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(content)
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
