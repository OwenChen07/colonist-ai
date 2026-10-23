"""Audit item 2: group human_dataset index statuses and reasons."""

import collections
import json

rows = [json.loads(l) for l in open("data/human/index.jsonl")]
status = collections.Counter(r["status"] for r in rows)
print("status:", dict(status))
keys = collections.Counter()
for r in rows:
    if r["status"] != "ok":
        keys.update(k for k in r if k not in ("capture", "account", "status"))
print("extra keys on non-ok rows:", dict(keys))
reasons = collections.Counter()
for r in rows:
    if r["status"] != "ok":
        why = r.get("reason") or r.get("error") or r.get("why") or ""
        reasons[(r["status"], str(why)[:90])] += 1
for k, v in reasons.most_common(40):
    print(v, k)
ended = collections.Counter(r.get("ended") for r in rows if r["status"] == "ok")
print("ok ended:", dict(ended))
by_acct = collections.defaultdict(collections.Counter)
for r in rows:
    by_acct[r["account"]][r["status"]] += 1
for a, c in sorted(by_acct.items()):
    print(a, dict(c))
