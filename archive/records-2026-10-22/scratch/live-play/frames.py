"""Print a capture's frames in time order: seq, seconds from the first, dir, type, a short body."""

import json
import sys

from catanatron.colonist.capture import decode

path = sys.argv[1]
raw = json.load(open(path))
msgs = raw["messages"]
t0 = min(m["t"] for m in msgs)
for m in msgs:
    f = decode(m)
    d = f.data
    text = json.dumps(d, default=str)
    if len(text) > 260:
        text = text[:260] + "..."
    print(
        f"{m['seq']:4d} {(m['t'] - t0) / 1000:8.2f} {m['dir']:3s} s{m.get('socket')} t={f.type} {text}"
    )
print("keys:", [k for k in raw if k != "messages"])
