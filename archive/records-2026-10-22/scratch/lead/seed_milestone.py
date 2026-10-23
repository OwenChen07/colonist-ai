"""One-off: post the current all-bots rating record to #milestones as the
channel's first entry (the watcher seeds the record silently by design)."""

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts/vm/orchestrator")
import core  # noqa: E402
import rank_watch  # noqa: E402

path = rank_watch.STATE / "rank-watch.json"
state = json.loads(path.read_text())
record = state["record"]
now = dt.datetime.now(dt.timezone.utc)
rank_watch.SHOTS.mkdir(parents=True, exist_ok=True)
shot = rank_watch.SHOTS / f"{record['name']}-record-{now:%Y%m%d-%H%M}.png"
rank_watch.screenshot(record["name"], shot)
state["queue"].append(
    {
        "text": f"📌 **Record to beat: {record['name']} at {record['rating']}** "
        f"(1v1 ranked, the highest rating any of the bots has held since this "
        f"watch began, {core.local(now):%b %d %-I:%M %p} Toronto). New highs "
        f"and #1 Global will be posted here.",
        "image": str(shot),
    }
)
path.write_text(json.dumps(state, indent=1))
print("queued", shot)
