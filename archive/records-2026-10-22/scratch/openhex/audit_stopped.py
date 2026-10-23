"""Audit: why the incomplete captures stop. Reads bridge.log around 'game saved to <capture>'."""

import collections
import json
import os
import re
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert

HOME = os.path.expanduser("~/colonist-ai/data")
SHOW = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rows = [json.loads(l) for l in open("data/human/index.jsonl")]
logs = {}
games = {}


def log_of(acct):
    if acct not in logs:
        p = f"{HOME}/{acct}/bridge.log"
        logs[acct] = (
            open(p, errors="replace").read().splitlines() if os.path.exists(p) else []
        )
        g = {}
        p = f"{HOME}/{acct}/games.jsonl"
        if os.path.exists(p):
            for l in open(p):
                try:
                    d = json.loads(l)
                    g[d["capture"]] = d
                except Exception:
                    pass
        games[acct] = g
    return logs[acct]


def classify(before):
    text = "\n".join(before)
    for key, pat in [
        ("out of sync", r"out of sync|isn't legal|could not reconcile"),
        ("move not confirmed", r"didn't show it|did not acknowledge|recorded"),
        ("could not send", r"could not send"),
        ("socket closed", r"keepalive|1011|1006|closed|disconnect"),
        ("stopped by signal", r"SIGTERM|stopping|shutdown|KeyboardInterrupt"),
        ("exception", r"Traceback|Error"),
        ("game over", r"game over"),
    ]:
        if re.search(pat, text):
            return key
    return "no reason in log"


reasons = collections.Counter()
out = []
for r in rows:
    if r["status"] != "incomplete":
        continue
    path = f"captures/{r['account']}/{r['capture']}"
    try:
        c = convert(load_capture(path))
        if c.unsupported:
            continue  # the monopoly cases, not stops
    except Exception:
        pass
    lines = log_of(r["account"])
    idx = [i for i, l in enumerate(lines) if r["capture"] in l]
    if not idx:
        why, before = "not in bridge.log", []
    else:
        i = idx[0]
        j = i - 1
        while j > 0 and "game saved to" not in lines[j] and i - j < 60:
            j -= 1
        after = lines[i + 1 : i + 8]
        before = lines[j + 1 : i] + ["-- saved --"] + after
        stops = [l.strip() for l in lines[max(0, i - 3) : i + 4] if "stopped:" in l]
        nxt = stops[0][9:90] if stops else "-"
        why = classify(lines[max(j + 1, i - 6) : i + 4])
        if why == "no reason in log":
            if any("bridge listening" in a for a in after[:3]):
                why = "bridge restarted"
            elif not after:
                why = "end of log"
            else:
                why = "next: " + after[0].strip()[:40]
    g = games.get(r["account"], {}).get(r["capture"], {})
    reasons[why] += 1
    out.append((r["account"], r["capture"], "|", nxt if idx else "-"))
    if SHOW:
        print("==", r["account"], r["capture"], why, g.get("ended"), g.get("queue"))
        for l in before[-SHOW:]:
            print("   ", l[:150])
for o in out:
    print(*o)
print(reasons.most_common())
