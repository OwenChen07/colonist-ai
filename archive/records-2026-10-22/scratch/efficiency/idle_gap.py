"""Per round: fleet idle minutes between self-play complete and the gate start
(self-play runs during the GPU training step), and how many more games would fit."""

import re
import sys

first = int(sys.argv[1]) if len(sys.argv) > 1 else 155
ts = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]")


def sec(line):
    m = ts.match(line)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


train, gate = {}, {}
for line in open("runs/train.log"):
    m = re.search(r"round (\d+): training on \d+ data folders", line)
    if m:
        train[int(m[1])] = sec(line)
    m = re.search(r"round (\d+): promotion match", line)
    if m:
        gate[int(m[1])] = sec(line)

print("round  sp_start-train  sp_min  idle_min  games_that_fit")
tot_idle = tot_sp = 0
for r in sorted(gate):
    if r < first or r not in train:
        continue
    try:
        lines = open(f"runs/g1/round_{r + 1}/prefetch.log").read().splitlines()
        s = next(
            sec(l) for l in lines if "self-play by" in l or "games, opponents" in l
        )
        e = next(sec(l) for l in lines if "self-play complete across" in l)
    except (OSError, StopIteration):
        continue
    lag = (s - train[r]) % 86400
    if lag > 43200:
        lag -= 86400
    sp = (e - s) % 86400
    idle = (gate[r] - e) % 86400
    if idle > 43200:
        idle -= 86400
    tot_idle += max(idle, 0)
    tot_sp += sp
    fit = 5000 * (sp + max(idle, 0)) / sp
    print(f"{r:5d} {lag/60:15.1f} {sp/60:7.1f} {idle/60:9.1f} {fit:15.0f}")
print(f"mean idle {tot_idle/60:.1f} min total; self-play {tot_sp/60:.1f} min")
