"""Peek at one capture: frame types and currentState examples."""

import collections
import json
import sys

from catanatron.colonist.capture import load as load_capture

frames = load_capture(sys.argv[1])
print(type(frames[0]), vars(frames[0]).keys() if hasattr(frames[0], "__dict__") else "")
types = collections.Counter((f.dir, f.type) for f in frames)
print(types)
shown = 0
for f in frames:
    d = f.data.get("data") if isinstance(f.data, dict) else None
    p = d.get("payload") if isinstance(d, dict) else None
    if not isinstance(p, dict):
        continue
    cs = None
    if "diff" in p and isinstance(p["diff"], dict):
        cs = p["diff"].get("currentState")
    elif "gameState" in p:
        cs = p["gameState"].get("currentState")
    if cs and shown < 25:
        print(
            f.seq, round(f.t / 1000 % 100000, 2) if f.t else None, json.dumps(cs)[:300]
        )
        shown += 1
outs = [f for f in frames if f.dir != "in"][:5]
for f in outs:
    print("OUT", f.type, json.dumps(f.data)[:300])
