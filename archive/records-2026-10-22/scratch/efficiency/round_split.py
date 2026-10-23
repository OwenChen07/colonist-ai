"""Per round: minutes waiting on self-play, training, and gate, from train.log."""

import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "runs/train.log"
first = int(sys.argv[2]) if len(sys.argv) > 2 else 140
ts = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] round (\d+): (.*)")
ev = {}
last = None


def sec(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


for line in open(path):
    m = ts.match(line)
    if not m:
        continue
    t = sec(*m.group(1, 2, 3))
    if last is not None and t < last - 3600:
        t += 86400  # crossed midnight; handled per pair below
    r, text = int(m.group(4)), m.group(5)
    if r < first:
        continue
    d = ev.setdefault(r, {})
    for key, pat in [
        ("wait", "waiting for its early self-play"),
        ("train", "training on \\d+ data"),
        ("gate", "promotion match"),
        ("done", "live value MSE"),
    ]:
        if re.match(pat, text) and key not in d:
            d[key] = t


def gap(a, b):
    x = b - a
    return (x % 86400) / 60


print("round  wait  train  gate  total")
for r in sorted(ev):
    d = ev[r]
    if not all(k in d for k in ("train", "gate", "done")):
        continue
    w = gap(d["wait"], d["train"]) if "wait" in d else 0.0
    print(
        f"{r:5d} {w:5.1f} {gap(d['train'], d['gate']):6.1f} "
        f"{gap(d['gate'], d['done']):5.1f} {w + gap(d['train'], d['done']):6.1f}"
    )
