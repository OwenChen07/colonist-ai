"""Turn a capture (recorder/ extension) into a game record (record.py).

colonist sends the full game once when a game starts (message type 4), then a
diff after every change (type 91). Each diff carries the changed parts of the
board and players, plus new game log entries. The converter walks those diffs,
emits Catanatron actions in log order, and adds a check wherever colonist
reports card counts, so the replay can confirm the two agree.

    python -m catanatron.colonist.convert captures/*.json -o records/

Coordinates (worked out from captured games):
  - Tiles: colonist axial (x, y) is Catanatron cube (x, -x-y, y).
  - Corners (x, y, z): z=0 is the tile's north node, z=1 its south node.
  - Edges (x, y, z): z=0 is the tile's northwest edge, z=1 west, z=2 southwest.
  - Ports sit on the same 9 edges as Catanatron's base map; only their types
    are shuffled.
Resources: 1 lumber, 2 brick, 3 wool, 4 grain, 5 ore (0 is the desert, and a
hidden card). Port type 1 is 3:1; types 2-6 are 2:1 for resources 1-5.

Supported so far: board, setup, rolls and income, settlements, cities, roads,
turn ends, resignation. Anything else in the log is reported as unsupported,
and the record stops there rather than guessing.
"""

import argparse
import glob
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.record import FORMAT, VERSION, save
from catanatron.colonist.rules import COLONIST_1V1
from catanatron.models.enums import (
    BRICK,
    ORE,
    SHEEP,
    WHEAT,
    WOOD,
    ActionType,
    EdgeRef,
    NodeRef,
)
from catanatron.models.map import (
    PORT_DIRECTION_TO_NODEREFS,
    LandTile,
    Port,
    Water,
    build_map,
)
from catanatron.serialization import map_from_json

RESOURCE = {1: WOOD, 2: BRICK, 3: SHEEP, 4: WHEAT, 5: ORE}
CORNER_NODE = {0: NodeRef.NORTH, 1: NodeRef.SOUTH}
EDGE_REF = {0: EdgeRef.NORTHWEST, 1: EdgeRef.WEST, 2: EdgeRef.SOUTHWEST}
SEAT_COLORS = ["RED", "BLUE", "ORANGE", "WHITE"]

GAME_STATE = 4
DIFF = 91

# Game log entry types (text.type).
LOG_PLACED = 4  # pieceEnum: 0 road, 2 settlement, 3 city
LOG_ROLLED = 10
LOG_SEPARATOR = 44
LOG_GOT_RESOURCES = 47
LOG_RESIGNED = 112
#: Seen in captures and safe to skip: game start banners, disconnect and
#: reconnect notices, and the winner announcement after a resignation.
LOG_IGNORED = {2, 22, 45, 47, 44}
PIECE = {0: "road", 2: "settlement", 3: "city"}
BUILDING = {1: "settlement", 2: "city"}


def cube(x, y):
    return (x, -x - y, y)


class ConvertError(ValueError):
    pass


@dataclass
class Conversion:
    record: dict
    unsupported: List[str] = field(default_factory=list)
    ended: Optional[str] = (
        None  # "resignation", "unsupported", or None if the capture just stops
    )

    def summary(self, name):
        n = len(self.record["log"])
        line = f"{name}: {n} log entries, {len(self.record['checks'])} checks"
        if self.ended:
            line += f", ended by {self.ended}"
        lines = [line] + [f"     - unsupported: {u}" for u in self.unsupported]
        return "\n".join(lines)


# ----- board -----
def convert_map(map_state):
    """colonist's mapState -> serialization.map_to_json document."""
    base = build_map("BASE")
    hexes = {cube(t["x"], t["y"]): t for t in map_state["tileHexStates"].values()}
    ports = {}
    for p in map_state["portEdgeStates"].values():
        ports[frozenset(_edge_nodes(base, p))] = p["type"]
    tiles = []
    for coordinate, tile in base.tiles.items():
        if isinstance(tile, LandTile):
            h = hexes.get(coordinate)
            if h is None:
                raise ConvertError(f"colonist board has no tile at {coordinate}")
            resource = RESOURCE.get(h["type"])  # None for the desert
            number = h["diceNumber"] if resource else None
            tiles.append(
                {
                    "coordinate": list(coordinate),
                    "type": "LAND",
                    "id": tile.id,
                    "resource": resource,
                    "number": number,
                }
            )
        elif isinstance(tile, Port):
            a, b = PORT_DIRECTION_TO_NODEREFS[tile.direction]
            nodes = frozenset((tile.nodes[a], tile.nodes[b]))
            if nodes not in ports:
                raise ConvertError(
                    f"no colonist port on the edge of port tile {coordinate}"
                )
            kind = ports[nodes]
            tiles.append(
                {
                    "coordinate": list(coordinate),
                    "type": "PORT",
                    "id": tile.id,
                    "resource": None if kind == 1 else RESOURCE[kind - 1],
                }
            )
        else:
            assert isinstance(tile, Water)
            tiles.append({"coordinate": list(coordinate), "type": "WATER"})
    return {"template": "BASE", "tiles": tiles}


def _corner_node(catan_map, c):
    return catan_map.tiles[cube(c["x"], c["y"])].nodes[CORNER_NODE[c["z"]]]


def _edge_nodes(catan_map, e):
    return catan_map.tiles[cube(e["x"], e["y"])].edges[EDGE_REF[e["z"]]]


# ----- game -----
def convert(frames) -> Conversion:
    start = next((f for f in frames if f.dir == "in" and f.type == GAME_STATE), None)
    if start is None:
        raise ConvertError(
            "capture has no game start; the extension must be on before the game begins"
        )
    payload = start.data["data"]["payload"]
    state = payload["gameState"]
    settings = payload.get("gameSettings", {})
    play_order = payload["playOrder"]
    seat = {c: SEAT_COLORS[i] for i, c in enumerate(play_order)}
    you = payload.get("playerColor")

    map_doc = convert_map(state["mapState"])
    catan_map = map_from_json(map_doc)
    corners = state["mapState"]["tileCornerStates"]
    edges = state["mapState"]["tileEdgeStates"]
    hexes = state["mapState"]["tileHexStates"]

    rules = {
        "vps_to_win": settings.get("victoryPointsToWin", COLONIST_1V1["vps_to_win"]),
        "discard_limit": settings.get(
            "cardDiscardLimit", COLONIST_1V1["discard_limit"]
        ),
        "friendly_robber": settings.get(
            "friendlyRobber", COLONIST_1V1["friendly_robber"]
        ),
        # diceSetting 1 has been balanced in every capture so far; the replay
        # check confirms it roll by roll.
        "dice": "balanced" if settings.get("diceSetting", 1) == 1 else "random",
    }
    users = {u.get("selectedColor"): u for u in payload.get("playerUserStates", [])}
    record = {
        "format": FORMAT,
        "version": VERSION,
        "rules": rules,
        "map": map_doc,
        "seats": [seat[c] for c in play_order],
        "players": {
            seat[c]: {
                "name": users.get(c, {}).get("username"),
                "you": c == you,
                "bot": users.get(c, {}).get("isBot"),
            }
            for c in play_order
        },
        "log": [],
        "checks": [],
        "source": {
            "colonist_game": settings.get("id"),
            "ranked": payload.get("gameDetails", {}).get("isRanked"),
            "started": start.t,
        },
    }
    robber = state.get("mechanicRobberState", {}).get("locationTileIndex")
    if robber is not None:
        h = hexes[str(robber)]
        record["robber"] = list(cube(h["x"], h["y"]))

    conv = Conversion(record)
    log = record["log"]
    setup_turns = 2 * len(play_order)
    completed = state["currentState"].get("completedTurns", 0)
    current = state["currentState"].get("currentTurnPlayerColor")

    def emit(color, action_type, value, result=None):
        log.append([[seat[color], action_type, value], result])

    for f in frames:
        if f.dir != "in" or f.type != DIFF or f.seq < start.seq:
            continue
        diff = f.data["data"]["payload"].get("diff", {})
        map_diff = diff.get("mapState", {})

        # Pieces placed in this diff, waiting to be put in log order.
        placed = []
        for idx, c in map_diff.get("tileCornerStates", {}).items():
            corners.setdefault(idx, {}).update(c)
            if "owner" in c and c.get("buildingType") in BUILDING:
                node = _corner_node(catan_map, corners[idx])
                placed.append((c["owner"], BUILDING[c["buildingType"]], node))
        for idx, e in map_diff.get("tileEdgeStates", {}).items():
            edges.setdefault(idx, {}).update(e)
            if "owner" in e and e.get("type") == 1:
                placed.append(
                    (e["owner"], "road", list(_edge_nodes(catan_map, edges[idx])))
                )

        stop = False
        for _, entry in sorted(
            diff.get("gameLogState", {}).items(), key=lambda kv: int(kv[0])
        ):
            text = entry.get("text", {})
            kind = text.get("type")
            if kind == LOG_PLACED:
                piece = PIECE.get(text.get("pieceEnum"))
                match = next(
                    (
                        p
                        for p in placed
                        if p[0] == text.get("playerColor") and p[1] == piece
                    ),
                    None,
                )
                if match is None:
                    conv.unsupported.append(
                        f"log entry {text} has no matching board change"
                    )
                    stop = True
                    break
                placed.remove(match)
                owner, piece, where = match
                action = {
                    "settlement": ActionType.BUILD_SETTLEMENT,
                    "city": ActionType.BUILD_CITY,
                    "road": ActionType.BUILD_ROAD,
                }[piece]
                emit(owner, action.value, where)
            elif kind == LOG_ROLLED:
                dice = [text["firstDice"], text["secondDice"]]
                emit(text["playerColor"], ActionType.ROLL.value, dice, dice)
            elif kind == LOG_RESIGNED:
                conv.ended = "resignation"
                stop = True
                break
            elif kind in LOG_IGNORED:
                continue
            else:
                conv.unsupported.append(f"log entry type {kind}: {text}")
                stop = True
                break
        if stop:
            if conv.ended is None:
                conv.ended = "unsupported"
            break
        if placed:
            conv.unsupported.append(f"board changes without a log entry: {placed}")
            conv.ended = "unsupported"
            break

        cur = diff.get("currentState", {})
        if "completedTurns" in cur:
            if completed >= setup_turns and cur["completedTurns"] > completed:
                emit(current, ActionType.END_TURN.value, None)
            completed = cur["completedTurns"]
        if "currentTurnPlayerColor" in cur:
            current = cur["currentTurnPlayerColor"]

        for color, ps in diff.get("playerStates", {}).items():
            cards = ps.get("resourceCards", {}).get("cards")
            if cards is None:
                continue
            check = {"at": len(log), "color": seat[int(color)], "resources": len(cards)}
            if int(color) == you:
                hand = {r: 0 for r in RESOURCE.values()}
                for card in cards:
                    hand[RESOURCE[card]] += 1
                check["hand"] = hand
            record["checks"].append(check)
    return conv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="capture files or globs")
    parser.add_argument("-o", "--out", default="records", help="output directory")
    args = parser.parse_args(argv)
    paths = sorted({p for pattern in args.paths for p in glob.glob(pattern)})
    if not paths:
        parser.error("no capture files matched")
    os.makedirs(args.out, exist_ok=True)
    for path in paths:
        name = os.path.basename(path).replace("colonist-capture-", "game-")
        try:
            conv = convert(load_capture(path))
        except ConvertError as e:
            print(f"SKIP {path}: {e}")
            continue
        out = os.path.join(args.out, name)
        save(conv.record, out)
        print(conv.summary(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
