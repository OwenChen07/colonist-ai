"""Print the order #experiments renders, with each entry's stamp and whether it parsed."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/owen/colonist-ai/scripts/vm/orchestrator")
import core  # noqa: E402

entries = json.loads(
    Path("/home/owen/colonist-ai/data/orchestrator/experiments.json").read_text()
)
if isinstance(entries, dict):
    entries = (
        entries.get("experiments") or entries.get("entries") or list(entries.values())
    )
print(f"{len(entries)} entries; keys of first: {sorted(entries[0].keys())}")
for name, statuses in core.EXPERIMENT_GROUPS:
    group = [e for e in entries if e.get("status") in statuses]
    if name == "Finished":
        known = {s for _, st in core.EXPERIMENT_GROUPS for s in st}
        group += [e for e in entries if e.get("status") not in known]
    print(f"\n== {name} ({len(group)})")
    for e in group:
        fin = e.get("status") in core.FINISHED_WORDS
        raw = (
            (e.get("ended") or e.get("started") or "")
            if fin
            else (e.get("started") or "")
        )
        print(
            f"  {e.get('id'):>5} {e.get('status'):<14} stamp={raw!r:<28} parsed={core.toronto_stamp(raw)}"
        )
