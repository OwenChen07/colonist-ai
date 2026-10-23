"""Print the JSON summary block from the first (real) result of a worker run log.

Usage: recover_summary.py RUN_LOG [field]
With no field, prints the whole JSON block; with 'progress' or 'post', just that field.
"""

import json
import re
import sys

log, field = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
results = []
for line in open(log, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") == "result":
        results.append(event)
for event in results:
    text = str(event.get("result") or "")
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        continue
    data = json.loads(m.group(1))
    origin = (event.get("origin") or {}).get("kind")
    print(
        f"# result_index {event.get('result_index')} origin {origin}", file=sys.stderr
    )
    print(data.get(field, "") if field else json.dumps(data, indent=1))
    break
else:
    sys.exit("no JSON summary in any result")
