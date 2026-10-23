"""Score E17 so far from e17-games.jsonl without touching the running job."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e17_match  # noqa: E402  (chdirs to ~/colonist-ai; main() is not run)

rows = e17_match.load()
e17_match.report(rows)
for start in range(0, len(rows), 100):
    e17_match.report(rows[start : start + 100])
