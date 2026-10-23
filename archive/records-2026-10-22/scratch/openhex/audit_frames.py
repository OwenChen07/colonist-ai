"""Print frames around a seq in a capture: audit_frames.py PATH SEQ [BEFORE] [AFTER]"""

import json
import sys

from catanatron.colonist.capture import load as load_capture

path, seq = sys.argv[1], int(sys.argv[2])
before = int(sys.argv[3]) if len(sys.argv) > 3 else 30
after = int(sys.argv[4]) if len(sys.argv) > 4 else 5
frames = load_capture(path)
for f in frames:
    if seq - before <= f.seq <= seq + after:
        d = f.data.get("data") if isinstance(f.data, dict) else None
        body = d if d is not None else f.data
        s = json.dumps(body)
        if "reconnect" in s.lower() or "token" in s.lower():
            s = "<omitted: may carry a token>"
        width = int(sys.argv[5]) if len(sys.argv) > 5 else 260
        print(f.seq, f.dir, f.type, round((f.t or 0) / 1000 % 1000, 2), s[:width])
