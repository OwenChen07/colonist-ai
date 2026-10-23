"""Replay a finished game and compare our end position with OpenHex's final state."""

import glob
import json
import sys

from catanatron.openhex.board import Board
from catanatron.openhex.follow import Follower, color

RES = ["wood", "brick", "sheep", "wheat", "ore"]

paths = sys.argv[1:] or sorted(glob.glob("data/openhex/games/*.json"))
for path in paths:
    g = json.load(open(path))
    fs = g.get("final_state")
    if not fs or not g.get("tiles"):
        continue
    fol = Follower(Board(g["tiles"], g["ports"]), g["turn_order"], g["me"], g["robber"])
    fol.feed(sorted(g["events"], key=lambda e: e["seq"]))
    fol.settle()
    game = fol.replay()
    s = game.state
    print(
        g["id"][:8],
        g["game"]["status"],
        "winner",
        s.winner() if hasattr(s, "winner") else game.winning_color(),
    )
    for p in fs["players"]:
        i = s.color_to_index[color(p["color"])]
        ps = s.player_state
        ours = {r: ps[f"P{i}_{r.upper()}_IN_HAND"] for r in RES}
        theirs = {r: p["hand"].get(r, 0) for r in RES}
        row = [
            p["color"],
            "hand",
            "ok" if ours == theirs or not p["hand"] else (ours, theirs),
            "size",
            sum(ours.values()),
            p.get("handSize", sum(theirs.values())),
            "knights",
            ps[f"P{i}_PLAYED_KNIGHT"],
            p["knightsPlayed"],
            "road",
            ps[f"P{i}_LONGEST_ROAD_LENGTH"],
            p["longestRoadLength"],
            "vp",
            ps[f"P{i}_ACTUAL_VICTORY_POINTS"],
        ]
        print("   ", *row)
    print("    robber", s.board.robber_coordinate, fs["board"].get("robberHex"))
