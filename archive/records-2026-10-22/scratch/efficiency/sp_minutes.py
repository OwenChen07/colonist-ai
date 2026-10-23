"""Self-play minutes per round: span of shard file times in runs/g1/round_NNN/data.

Usage: sp_minutes.py <first round> <last round>
Prints first shard, last shard, span, the 95th-percentile arrival and the games.
"""

import os
import re
import sys
import time

base = os.path.expanduser("~/colonist-ai/runs/g1")
log = open(os.path.join(base, "..", "train.log")).read().splitlines()
lo, hi = int(sys.argv[1]), int(sys.argv[2])
for r in range(lo, hi + 1):
    start = None
    for line in log:
        m = re.match(r"\[(\d\d:\d\d:\d\d)\] round %d: self-play started" % r, line)
        if m:
            start = m.group(1)
    d = os.path.join(base, "round_%03d" % r, "data")
    if not os.path.isdir(d):
        continue
    items = []
    for f in os.listdir(d):
        m = re.search(r"_(\d+)\.npz$", f)
        if m:
            items.append((os.path.getmtime(os.path.join(d, f)), int(m.group(1))))
    if not items:
        continue
    items.sort()
    t0 = items[0][0]
    if start:
        lt = time.localtime(t0)
        h, mi, s = map(int, start.split(":"))
        # train.log times are UTC
        st = time.mktime(time.gmtime(t0))
        day0 = st - (
            time.gmtime(t0).tm_hour * 3600
            + time.gmtime(t0).tm_min * 60
            + time.gmtime(t0).tm_sec
        )
        t_start = t0 - (st - (day0 + h * 3600 + mi * 60 + s))
    else:
        t_start = t0
    games = sum(g for _, g in items)
    tot = 0
    p95 = None
    for t, g in items:
        tot += g
        if p95 is None and tot >= 0.95 * games:
            p95 = t
    print(
        "round %d: start %s, files %d, games %d, to 95%% %.1f min, to last %.1f min, tail %.1f min"
        % (
            r,
            start,
            len(items),
            games,
            (p95 - t_start) / 60,
            (items[-1][0] - t_start) / 60,
            (items[-1][0] - p95) / 60,
        )
    )
