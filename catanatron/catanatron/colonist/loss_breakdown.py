"""Cheap loss breakdown from data/human/index.jsonl and data/<account>/games.jsonl.

No replay, so it runs in seconds. Joins each ranked game's index row (rolls,
buildings, how it ended) with its games.jsonl row (time, account, net) and
prints bot win rates (Wilson 95% intervals) by game length, by how the game
ended, and by the winner's cities/settlements/roads. Prints no names.

    nice -n 19 python -m catanatron.colonist.loss_breakdown [--last 300]
"""

import argparse
import glob
import json
import sys

from catanatron.colonist.loss_report import wilson

LENGTHS = ((0, 59, "<60 rolls"), (60, 89, "60-89"), (90, 10**6, "90+"))


def load(data="data"):
    index = {}
    with open(f"{data}/human/index.jsonl") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("status") == "ok":
                index[row["capture"]] = row
    games = []
    for path in glob.glob(f"{data}/*/games.jsonl"):
        with open(path) as stream:
            for line in stream:
                row = json.loads(line)
                hit = index.get(row["capture"])
                if row.get("queue") == "ranked" and hit:
                    games.append({**hit, **row})
    games.sort(key=lambda g: g["at"])
    return games


def lines(window):
    rate = lambda sel: wilson(sum(g["bot_won"] for g in sel), len(sel))  # noqa: E731
    out = [f"window {len(window)} ranked games, bot wins {rate(window)}"]
    for lo, hi, label in LENGTHS:
        out.append(
            f"length {label}: bot wins {rate([g for g in window if lo <= g['rolls'] <= hi])}"
        )
    for ended in ("win", "resignation"):
        sel = [g for g in window if g["ended"] == ended]
        out.append(f"ended by {ended}: bot wins {rate(sel)}")
    losses = [g for g in window if not g["bot_won"]]
    out.append(f"losses: {len(losses)}")
    for key in ("cities", "settlements", "roads"):
        diff = [g for g in window if g["bot"][key] != g["human"][key]]
        more = [g for g in diff if g["bot"][key] > g["human"][key]]
        out.append(
            f"built more {key} than the person: bot wins {rate(more)}; "
            f"fewer: {rate([g for g in diff if g not in more])}"
        )
    exported = [g for g in window if g.get("exported")]
    ahead = [g for g in exported if g["exported"]["value"] > 50]
    out.append(f"exported value known for {len(exported)} games ({len(ahead)} > 50)")
    for acct in sorted({g["account"] for g in window}):
        out.append(
            f"account {acct}: bot wins {rate([g for g in window if g['account'] == acct])}"
        )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", default="data")
    parser.add_argument("--last", type=int, default=300)
    args = parser.parse_args(argv)
    games = load(args.data)
    for line in lines(games[-args.last :]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
