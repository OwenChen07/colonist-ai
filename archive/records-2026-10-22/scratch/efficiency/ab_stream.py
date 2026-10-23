"""Plays seeds first..first+total-1 through selfplay.run's --parts-stream path
(one process, parts of `part`), production settings (round 73), and prints the
wall time, using the worktree's code. usage: ab_stream.py <out_dir> <first> <total> <part>
"""

import os
import sys
import time

sys.path.insert(0, "/home/owen/colonist-ai-wt/efficiency/catanatron")

from catanatron.colonist import selfplay


def main():
    print("code:", selfplay.__file__, flush=True)
    out_dir, first, total, part = sys.argv[1], *map(int, sys.argv[2:5])
    width = int(sys.argv[5]) if len(sys.argv) > 5 else selfplay.STREAM_GAMES
    nets = "runs/g1/nets"
    stream = [
        (f, min(part, first + total - f)) for f in range(first, first + total, part)
    ]
    t0 = time.perf_counter()
    selfplay.run(
        f"{nets}/round_073.npz",
        out_dir,
        simulations=400,
        workers=1,
        opponents=(f"{nets}/round_070.npz", f"{nets}/round_071.npz", "AB:2", "F"),
        opponent_rate=0.2,
        full_rate=0.25,
        fast_simulations=24,
        part_stream=iter(stream),
        done=lambda name: print("done", name, flush=True),
        log=lambda m: print(m, flush=True),
        stream_games=width,
    )
    wall = time.perf_counter() - t0
    print(
        f"STREAM {part} width {width}: {total} games {wall:.1f} s = {wall / total:.2f} s/game",
        flush=True,
    )


if __name__ == "__main__":
    main()
