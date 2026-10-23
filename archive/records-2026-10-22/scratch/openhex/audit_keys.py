"""Print the keys of colonist's gameState in a capture's last full snapshot
and of the diffs, with sample values for the keys named on the command line.
Usage: audit_keys.py PATH [KEY ...]"""

import collections
import json
import sys

from catanatron.colonist import convert as C
from catanatron.colonist.capture import load as load_capture

frames = load_capture(sys.argv[1])
diff_keys = collections.Counter()
last = None
for f in frames:
    if f.dir != "in":
        continue
    if f.type == C.GAME_STATE:
        p = f.data["data"].get("payload")
        if isinstance(p, dict) and "gameState" in p:
            last = p["gameState"]
    elif f.type == C.DIFF:
        for k in (f.data["data"].get("payload") or {}).get("diff", {}):
            diff_keys[k] += 1
print("snapshot keys:", sorted(last) if last else None)
print("diff keys:", dict(diff_keys))
for k in sys.argv[2:]:
    print(k, json.dumps(last.get(k) if last else None)[:600])
