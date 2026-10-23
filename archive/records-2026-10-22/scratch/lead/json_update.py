"""Apply a spec of updates/appends to data/orchestrator/experiments.json.

Usage: json_update.py SPEC.json
SPEC: {"update": [{"id": "E54", "status": "...", ...}], "append": [{full entry}, ...]}
Fields in "update" overwrite the entry's fields; "result_prepend" prepends to result.
Prints the ids touched; refuses to write if the file would not parse.
"""

import json
import sys

PATH = "data/orchestrator/experiments.json"


def main() -> None:
    spec = json.load(open(sys.argv[1]))
    data = json.load(open(PATH))
    entries = data["experiments"] if isinstance(data, dict) else data
    by_id = {e.get("id"): e for e in entries}
    for upd in spec.get("update", []):
        e = by_id.get(upd["id"])
        if e is None:
            print("missing id", upd["id"])
            continue
        for k, v in upd.items():
            if k == "id":
                continue
            if k == "result_prepend":
                e["result"] = v + (" " + e.get("result", "") if e.get("result") else "")
            else:
                e[k] = v
        print("updated", upd["id"])
    for new in spec.get("append", []):
        if new["id"] in by_id:
            print("exists, skipped", new["id"])
            continue
        entries.append(new)
        print("appended", new["id"])
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    json.loads(text)
    open(PATH, "w").write(text)
    print("ok", len(entries), "entries")


if __name__ == "__main__":
    main()
