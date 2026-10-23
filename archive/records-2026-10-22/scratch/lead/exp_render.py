"""Print the ids in the order the new #experiments renderer (lead's worktree) lists them."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/owen/colonist-ai-wt/lead/scripts/vm/orchestrator")
import core  # noqa: E402

entries = json.loads(
    Path("/home/owen/colonist-ai/data/orchestrator/experiments.json").read_text()
)
if isinstance(entries, dict):
    entries = (
        entries.get("experiments") or entries.get("entries") or list(entries.values())
    )
text = "\n".join(t for m in core.experiment_messages(entries) for t in m)
for line in text.splitlines():
    if line.startswith("__**") or re.match(r"\*\*E\d+", line):
        print(line[:110])
