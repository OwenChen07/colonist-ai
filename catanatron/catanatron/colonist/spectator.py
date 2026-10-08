"""Public, deliberately redacted snapshots for the ranked spectator site.

Never publish a Colonist frame or a game record: both can contain account
details, chat, reconnect material, and hidden cards. This module constructs a
small allowlisted projection from the simulator's checked position instead.
"""

import hashlib
import json
import math
import os
import tempfile
import time

from catanatron.models.enums import NodeRef
from catanatron.state_functions import (
    get_visible_victory_points,
    player_num_dev_cards,
    player_num_resource_cards,
)


NODE_ANGLES = {
    NodeRef.NORTH: -90,
    NodeRef.NORTHEAST: -30,
    NodeRef.SOUTHEAST: 30,
    NodeRef.SOUTH: 90,
    NodeRef.SOUTHWEST: 150,
    NodeRef.NORTHWEST: 210,
}


def public_snapshot(live):
    """Return only board geometry, public counts, and non-secret move names."""
    game = live.game
    if game is None:
        return None
    state = game.state
    board = state.board
    game_id = (live.record.get("source") or {}).get("colonist_game")
    game_key = hashlib.sha256(str(game_id).encode()).hexdigest()[:16]
    nodes = {}
    tiles = []
    for (q, _, r), tile in board.map.land_tiles.items():
        x = math.sqrt(3) * (q + r / 2)
        y = 1.5 * r
        tiles.append(
            {
                "q": q,
                "r": r,
                "resource": tile.resource,
                "number": tile.number,
                "robber": board.robber_coordinate == (q, -q - r, r),
            }
        )
        for ref, node_id in tile.nodes.items():
            angle = math.radians(NODE_ANGLES[ref])
            nodes[node_id] = {
                "id": node_id,
                "x": round(x + math.cos(angle), 4),
                "y": round(y + math.sin(angle), 4),
            }
    buildings = [
        {"node": node, "color": owner.value, "type": kind}
        for node, (owner, kind) in board.buildings.items()
    ]
    roads = [
        {"a": a, "b": b, "color": owner.value}
        for (a, b), owner in board.roads.items()
        if a < b
    ]
    players = [
        {
            "color": color.value,
            "bot": color == live.bot,
            "points": get_visible_victory_points(state, color),
            "resources": player_num_resource_cards(state, color),
            "developmentCards": player_num_dev_cards(state, color),
        }
        for color in state.colors
    ]
    moves = [
        {"color": action[0], "type": action[1]}
        for action, _ in live.record["log"][-10:]
    ]
    winner = live.record.get("winner")
    phase = "finished" if live.over or winner else "paused" if live.error else "live"
    return {
        "gameKey": game_key,
        "phase": phase,
        "observedAt": int(time.time() * 1000),
        "moveNumber": len(live.record["log"]),
        "turn": state.num_turns,
        "toMove": state.current_color().value if phase == "live" else None,
        "winner": winner,
        "botColor": live.bot.value if live.bot else None,
        "tiles": tiles,
        "nodes": list(nodes.values()),
        "buildings": buildings,
        "roads": roads,
        "players": players,
        "recentMoves": moves,
    }


class SnapshotWriter:
    """Keep a bounded in-game rewind window and atomically publish it locally."""

    def __init__(self, path, max_frames=55, min_write_interval=3):
        self.path = path
        self.max_frames = max_frames
        self.min_write_interval = min_write_interval
        self.game_key = None
        self.frames = []
        self.last_move = None
        self.last_write = 0.0

    def update(self, live, force=False):
        snap = public_snapshot(live)
        if snap is None:
            return
        if snap["gameKey"] != self.game_key:
            self.game_key = snap["gameKey"]
            self.frames = []
            self.last_move = None
        if snap["moveNumber"] != self.last_move or not self.frames:
            self.frames.append(snap)
            self.frames = self.frames[-self.max_frames :]
            self.last_move = snap["moveNumber"]
        elif snap["phase"] != self.frames[-1]["phase"]:
            self.frames[-1] = snap
        now = time.monotonic()
        if not force and now - self.last_write < self.min_write_interval:
            return
        self.last_write = now
        doc = {"schema": 1, "live": snap, "rewind": self.frames}
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as out:
            temp = out.name
            json.dump(doc, out, separators=(",", ":"))
        os.replace(temp, self.path)
