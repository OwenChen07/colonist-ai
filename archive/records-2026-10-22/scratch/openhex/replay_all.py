"""Replay every saved OpenHex game through Follower; report where each breaks."""

import glob
import json
import sys

from catanatron.openhex.board import Board
from catanatron.openhex.follow import Follower

paths = sys.argv[1:] or sorted(glob.glob("data/openhex/games/*.json"))
for path in paths:
    with open(path) as f:
        g = json.load(f)
    gid = g["id"][:8]
    if not g.get("tiles"):
        print(gid, "no tiles")
        continue
    fol = Follower(Board(g["tiles"], g["ports"]), g["turn_order"], g["me"], g["robber"])
    events = sorted({e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"])
    seqs = [e["seq"] for e in events]
    gaps = [s for a, s in zip(seqs, seqs[1:]) if s != a + 1]
    status = (g.get("game") or {}).get("status")
    try:
        fol.feed(events)
        fol.settle()
        game = fol.replay()
        print(
            gid,
            status,
            "OK",
            len(events),
            "events",
            len(fol.log),
            "log",
            "unknown",
            sorted(fol.unknown),
            "gaps",
            gaps[:3],
            "deferred",
            len(fol.deferred),
            "turns",
            game.state.num_turns,
        )
    except Exception as e:
        print(
            gid, status, "FAIL", len(events), "events", "gaps", gaps[:3], repr(e)[:400]
        )
        # context: events around the failing log entry
        msg = str(e)
        if "log entry" in msg:
            i = int(msg.split("log entry ")[1].split()[0])
            for row in fol.log[max(0, i - 6) : i + 1]:
                print("    log", row)
