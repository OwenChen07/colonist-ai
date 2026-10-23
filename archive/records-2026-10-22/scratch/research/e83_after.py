"""E83's follow-ups, unattended: the people holdout check at steps 40, 50, 60
(each as soon as the step's raw score is in eval.txt), and the gate against
round 189 (2,000 games at 200 sims, normal priority this once, the owner's
deadline) on the best of steps 40/50/60 by raw score, the moment step 60's
score lands. Outputs in data/orchestrator/e83/.

    .venv/bin/python data/orchestrator/scratch/research/e83_after.py
"""

import os
import re
import subprocess
import time

ROOT = "/home/owen/colonist-ai"
RUN = f"{ROOT}/data/orchestrator/e54/run1"
OUT = f"{ROOT}/data/orchestrator/e83"
PY = f"{ROOT}/.venv/bin/python"
R = f"{ROOT}/data/orchestrator/scratch/research"
R189 = f"{ROOT}/runs/g1/nets/round_189.npz"


def scores():
    found = {}
    path = f"{RUN}/eval.txt"
    for line in open(path) if os.path.exists(path) else []:
        m = re.match(r"(\d+)\t.*?: ([\d.]+)% ±", line)
        if m:
            found[int(m.group(1))] = float(m.group(2))
    return found


def holdout(step):
    net = f"{RUN}/iter-{step}/net.npz"
    cmd = [PY, f"{R}/e54_holdout.py", f"i{step}={net}", f"i30={RUN}/iter-30/net.npz"]
    with open(f"{OUT}/holdout-{step}.txt", "w") as f:
        return subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)


def main():
    started, procs = set(), []
    while True:
        s = scores()
        for step in (40, 50, 60):
            if step in s and step not in started:
                started.add(step)
                print(f"step {step}: raw {s[step]}%; holdout started", flush=True)
                procs.append(holdout(step))
        if 60 in s:
            break
        time.sleep(30)
    best = max((40, 50, 60), key=lambda k: (s.get(k, 0), k))
    print(f"gate: step {best} (raw {s.get(40)}, {s.get(50)}, {s.get(60)})", flush=True)
    env = dict(os.environ, COLONIST_MATCH_NICE="0")
    cmd = [PY, f"{R}/gate.py", f"e83i{best}-vs-r189"]
    cmd += [f"Z:{RUN}/iter-{best}/net.npz:200", f"Z:{R189}:200"]
    cmd += ["--games", "2000", "--seed", "8300000000", "--out", f"{OUT}/gate.txt"]
    subprocess.run(cmd, cwd=ROOT, env=env)
    for p in procs:
        p.wait()
    print("done", flush=True)


if __name__ == "__main__":
    main()
