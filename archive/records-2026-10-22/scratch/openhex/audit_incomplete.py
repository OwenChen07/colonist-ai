"""Audit item 2: why captures are incomplete or error in the human dataset."""

import collections
import json
import traceback

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert

rows = [json.loads(l) for l in open("data/human/index.jsonl")]
reasons = collections.Counter()
examples = {}
for r in rows:
    if r["status"] not in ("incomplete", "error"):
        continue
    path = f"captures/{r['account']}/{r['capture']}"
    try:
        c = convert(load_capture(path))
        why = "; ".join(u[:80] for u in c.unsupported) or f"ended={c.ended}"
    except Exception as e:  # noqa
        tb = traceback.extract_tb(e.__traceback__)[-1]
        why = f"{type(e).__name__}: {str(e)[:60]} at {tb.name}:{tb.lineno}"
    reasons[why] += 1
    examples.setdefault(why, path)
for k, v in reasons.most_common():
    print(v, k, "|", examples[k])
