"""E71 step 0: late starts from people's holdout games (turns 50-90), up to
three cuts a game, each with the bot's and the person's chair, who won live
and the person's rating, for e71_late.py's matches.

    .venv/bin/python data/orchestrator/scratch/research/e71_starts.py \
        [--min-turn 50] [--max-turn 90] [--out starts]

Writes data/orchestrator/e71/OUT.jsonl (one start a line).
"""

import argparse
import glob
import json
import os
import random
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist import starts as S  # noqa: E402
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from lvlib import load_rows  # noqa: E402

OUT = "data/orchestrator/e71"
PER_GAME = 3
p = argparse.ArgumentParser()
p.add_argument("--min-turn", type=int, default=50)
p.add_argument("--max-turn", type=int, default=90)
p.add_argument("--out", default="starts")
args = p.parse_args()

mmr = {}
caps = {}
for path in glob.glob("data/*/games.jsonl"):
    account = path.split("/")[1]
    for line in open(path):
        if line.strip():
            g = json.loads(line)
            if g.get("capture"):
                caps[(account, g["at"])] = g["capture"]
for r in load_rows():
    c = caps.get((r["a"], r["at"]))
    if c:
        mmr[(r["a"], c)] = r["opp_mmr"]

rows = [json.loads(line) for line in open("data/human/index.jsonl") if line.strip()]
rows = [r for r in rows if r.get("status") == "ok" and r.get("split") == "holdout"]
os.makedirs(OUT, exist_ok=True)
games = n = 0
with open(f"{OUT}/{args.out}.jsonl.part", "w") as out:
    for r in rows:
        try:
            record = convert(
                load_capture(f"captures/{r['account']}/{r['capture']}")
            ).record
            entry = S.start_entry(record, args.min_turn, args.max_turn)
        except Exception as error:  # noqa: BLE001
            print("skip", r["capture"], error)
            continue
        if entry is None:
            continue
        bot = [c for c, p in record["players"].items() if p["you"]][0]
        person = [c for c in record["seats"] if c != bot][0]
        rng = random.Random(r["game"])
        cuts = rng.sample(entry["cuts"], min(PER_GAME, len(entry["cuts"])))
        games += 1
        for cut in sorted(cuts):
            line = {
                "account": r["account"],
                "capture": r["capture"],
                "bot": bot,
                "person": person,
                "bot_won": r["bot_won"],
                "opp_mmr": mmr.get((r["account"], r["capture"])),
                "cut": cut,
                "start": S.pack(entry, cut),
            }
            out.write(json.dumps(line) + "\n")
            n += 1
os.rename(f"{OUT}/{args.out}.jsonl.part", f"{OUT}/{args.out}.jsonl")
rated = sum(1 for r in rows if mmr.get((r["account"], r["capture"])) is not None)
print(f"{games} games of {len(rows)} holdout, {n} starts; {rated} games rated")
