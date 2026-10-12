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
Development cards: 10 hidden, 11 knight, 12 victory point, 13 monopoly,
14 road building, 15 year of plenty.
Points (victoryPointsState): 0 settlements, 1 cities, 2 victory point cards,
3 largest army, 4 longest road.

Supported: board, setup, rolls and income, building, turn ends, 7s (discards,
robber, steals), development cards (buying and all four plays), bank and port
trades, the end of the game. Anything else in the log is reported as
unsupported, and the record stops there rather than guessing.
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
    KNIGHT,
    MONOPOLY,
    ORE,
    ROAD_BUILDING,
    SHEEP,
    VICTORY_POINT,
    WHEAT,
    WOOD,
    YEAR_OF_PLENTY,
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
GAME_OVER = 45
#: The end-of-game summary counts points by kind: settlements, cities, victory
#: point cards, largest army, longest road. The value of one of each:
END_GAME_POINTS = {"0": 1, "1": 2, "2": 1, "3": 2, "4": 2}

DEV_CARD = {
    11: KNIGHT,
    12: VICTORY_POINT,
    13: MONOPOLY,
    14: ROAD_BUILDING,
    15: YEAR_OF_PLENTY,
}
HIDDEN_CARD = 10

# Game log entry types (text.type).
LOG_BOUGHT_DEV_CARD = 1
LOG_PLACED_SETUP = 4  # pieceEnum: 0 road, 2 settlement, 3 city
LOG_BUILT = 5  # same, during the game
LOG_ROLLED = 10
LOG_MOVED_ROBBER = 11
LOG_YOU_STOLE = 14  # from the perspective of the recording player
LOG_STOLE_FROM_YOU = 15
LOG_PLAYED_DEV_CARD = 20
LOG_YEAR_OF_PLENTY = 21  # the cards taken
LOG_WON = 45
LOG_DISCARDED = 55
LOG_TIMER_DISCARDED = 64  # colonist's timer chose the discards (55 follows)
LOG_ROBBER_NOTHING_TO_STEAL = 74
STEALS = (LOG_YOU_STOLE, LOG_STOLE_FROM_YOU, LOG_ROBBER_NOTHING_TO_STEAL)
LOG_MONOPOLY = 86  # the resource taken
LOG_RESIGNED = 112
LOG_TRADED_WITH_BANK = 116
LOG_BANK_SHORTAGE = 146  # informational: a roll could not pay all players
LOG_PIECE_NOTICE = {72, 87}  # before matching setup road / settlement placement
#: Safe to skip: a player joining (0), game start banners (2), connection
#: status notices (22, 23; the playerStates diff carries isConnected), the
#: 10-second rule being turned off (24), separators (44),
#: income (47, checked through hand counts instead), tiles blocked by the
#: robber (49), colonist's timer starting to move the robber for a player
#: (58; the move itself follows as 11), "move the robber" prompts (60),
#: colonist's timer choosing a player's discards (64; the discard follows as 55),
#: longest road / largest army
#: changing hands or being lost (66, 67, 68), which Catanatron works out itself, trade offers
#: between players (118: an offer alone changes nothing; an accepted trade
#: logs its own entry, which is not mapped yet and so stops the record), and
#: a pre-game counter (130), and how many players the robber can steal
#: from (139, logged with the robber move; the steal itself may follow in a
#: later update, seen live), and a player disconnecting (140, logged with
#: isConnected false in the same update, seen live in a ranked game), and a
#: player starting a vote to pause the game (124, sent with a type 51 vote
#: frame; the vote needs every player's accept, so it expires unless the bot
#: votes, seen live in a ranked game), and a player starting a vote to
#: disable karma (36, sent with a type 51 vote frame titled
#: disableKarmaInitiator; it also needs every player's accept, seen live in a
#: ranked game), and a bare notice with no fields (128) logged alone right
#: after a pause vote expired and colonist reset the client with paused false
#: (seen live in a ranked game; it changes nothing), and a bare notice with
#: no fields (34) logged right after a disable-karma vote expired (seen live
#: in a ranked game, in the same update as the next move; it changes nothing),
#: and a bare notice with no fields (131) logged alone after the game-over
#: frame (45), when a disable-karma vote had closed as the game ended (seen
#: live in a ranked game; it changes nothing).
LOG_IGNORED = {
    0,
    2,
    22,
    23,
    24,
    34,
    36,
    44,
    47,
    49,
    58,
    60,
    64,
    66,
    67,
    68,
    118,
    124,
    128,
    130,
    131,
    139,
    140,
}
PIECE = {0: "road", 2: "settlement", 3: "city"}
BUILDING = {1: "settlement", 2: "city"}
BUILD_ACTION = {
    "settlement": ActionType.BUILD_SETTLEMENT.value,
    "city": ActionType.BUILD_CITY.value,
    "road": ActionType.BUILD_ROAD.value,
}


def cube(x, y):
    return (x, -x - y, y)


class ConvertError(ValueError):
    pass


@dataclass
class Conversion:
    record: dict
    unsupported: List[str] = field(default_factory=list)
    #: "win", "resignation", "unsupported", or None if the capture just stops
    ended: Optional[str] = None

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
class _Unsupported(Exception):
    pass


class _Converter:
    pending_robber = None  # (color, tile) awaiting its steal entry

    def __init__(self, payload, start):
        self.payload = payload
        state = payload["gameState"]
        settings = payload.get("gameSettings", {})
        self.play_order = payload["playOrder"]
        self.seat = {c: SEAT_COLORS[i] for i, c in enumerate(self.play_order)}
        self.you = payload.get("playerColor")

        map_doc = convert_map(state["mapState"])
        self.catan_map = map_from_json(map_doc)
        self.corners = state["mapState"]["tileCornerStates"]
        self.edges = state["mapState"]["tileEdgeStates"]
        self.hexes = state["mapState"]["tileHexStates"]
        self.robber_tile = state.get("mechanicRobberState", {}).get("locationTileIndex")
        # The highest game-log id applied, for reconnect snapshots.
        self.last_log_id = max(
            (int(k) for k in (state.get("gameLogState") or {})), default=-1
        )
        self.ratios = {
            int(c): {int(r): n for r, n in ps.get("bankTradeRatiosState", {}).items()}
            for c, ps in state["playerStates"].items()
        }
        self.points = {
            int(c): dict(ps.get("victoryPointsState", {}))
            for c, ps in state["playerStates"].items()
        }

        rules = {
            "vps_to_win": settings.get(
                "victoryPointsToWin", COLONIST_1V1["vps_to_win"]
            ),
            "discard_limit": settings.get(
                "cardDiscardLimit", COLONIST_1V1["discard_limit"]
            ),
            "friendly_robber": settings.get(
                "friendlyRobber", COLONIST_1V1["friendly_robber"]
            ),
            # diceSetting 1 has been balanced in every capture so far; the
            # replay check confirms it roll by roll.
            "dice": "balanced" if settings.get("diceSetting", 1) == 1 else "random",
        }
        users = {u.get("selectedColor"): u for u in payload.get("playerUserStates", [])}
        self.record = {
            "format": FORMAT,
            "version": VERSION,
            "rules": rules,
            "map": map_doc,
            "seats": [self.seat[c] for c in self.play_order],
            "players": {
                self.seat[c]: {
                    "name": users.get(c, {}).get("username"),
                    "you": c == self.you,
                    "bot": users.get(c, {}).get("isBot"),
                }
                for c in self.play_order
            },
            "log": [],
            "checks": [],
            "source": {
                "colonist_game": settings.get("id"),
                "ranked": payload.get("gameDetails", {}).get("isRanked"),
                "started": start.t,
            },
        }
        if self.robber_tile is not None:
            self.record["robber"] = list(self._tile_cube(self.robber_tile))
        self.conv = Conversion(self.record)
        self.log = self.record["log"]
        self.setup_turns = 2 * len(self.play_order)
        self.completed = state["currentState"].get("completedTurns", 0)
        self.current = state["currentState"].get("currentTurnPlayerColor")
        self.discards = []  # waiting for the robber, then emitted in seat order
        # Where the current 7's discards start in the log, until the next move.
        self.discard_phase = None
        self.pending_play = None  # a monopoly or year of plenty awaiting its cards
        self.your_cards = []  # the recording player's resource cards (colonist ids)

    # ----- helpers -----
    def emit(self, color, action_type, value, result=None):
        self._resolve_unfinished_play()
        self._resolve_robber()
        self._flush_discards()
        self.discard_phase = None
        self.log.append([[self.seat[color], action_type, value], result])

    def _flush_discards(self):
        """Emit the waiting discards. Catanatron asks seats to discard in seat
        order, but colonist may report a later seat's discards in an earlier
        update (when its timer discards for each player separately, seen live):
        a seat's discards then go before the later seat's ones already in the
        log. Its own checks since the 7 were taken before it discarded, so they
        move to where its discards start; the others' move along with the log."""
        if not self.discards:
            return
        discards, self.discards = self.discards, []
        if self.discard_phase is None:
            self.discard_phase = len(self.log)
        seats = self.record["seats"]
        for color in sorted({c for c, _ in discards}, key=self.play_order.index):
            seat = self.seat[color]
            entries = [
                [[seat, ActionType.DISCARD_RESOURCE.value, card], card]
                for c, card in discards
                if c == color
            ]
            at = next(
                (
                    i
                    for i in range(self.discard_phase, len(self.log))
                    if seats.index(self.log[i][0][0]) > seats.index(seat)
                ),
                len(self.log),
            )
            if at < len(self.log):
                for check in self.record["checks"]:
                    if check["at"] > at:
                        check["at"] = (
                            at if check["color"] == seat else check["at"] + len(entries)
                        )
            self.log[at:at] = entries

    def _resolve_robber(self, steal=None):
        """Emit the waiting robber move: with the card stolen in `steal`, or,
        without one, as a move that robbed nobody."""
        if self.pending_robber is None:
            return
        (color, coordinate), self.pending_robber = self.pending_robber, None
        if steal is None or steal["type"] == LOG_ROBBER_NOTHING_TO_STEAL:
            victim, card = None, None
        else:
            victim = self.seat[self._other(color)]
            card = RESOURCE[steal["cardEnums"][0]]
        self.emit(color, ActionType.MOVE_ROBBER.value, [coordinate, victim], card)

    def _tile_cube(self, index):
        h = self.hexes[str(index)]
        return cube(h["x"], h["y"])

    def _other(self, color):
        others = [c for c in self.play_order if c != color]
        if len(others) != 1:
            raise _Unsupported("steals are only mapped for 1v1 games")
        return others[0]

    def _next_placement(self, placed, color, piece):
        """The board change for a build log entry. One diff usually holds one,
        but a reconnect snapshot can hold several (two roads in a row, seen
        live): take one that joins what the player already has on the board,
        so the log stays in an order the game allows."""
        candidates = [p for p in placed if p[0] == color and p[1] == piece]
        if len(candidates) < 2:
            return candidates[0] if candidates else None
        seat = self.seat[color]
        mine = set()
        for (who, kind, value), _ in self.log:
            if who != seat:
                continue
            if kind == ActionType.BUILD_ROAD.value:
                mine.update(value)
            elif kind in (
                ActionType.BUILD_SETTLEMENT.value,
                ActionType.BUILD_CITY.value,
            ):
                mine.add(value)
        for p in candidates:
            nodes = p[2] if isinstance(p[2], list) else [p[2]]
            if mine.intersection(nodes):
                return p
        return candidates[0]

    def reconcile(self, state):
        """Apply the part of a same-game snapshot (colonist resends the whole
        game after a reconnect) that no diff delivered: the newer log entries
        and the board changes with them. Returns whether anything was new."""
        logs = state.get("gameLogState") or {}
        new_logs = {k: v for k, v in logs.items() if int(k) > self.last_log_id}
        if not new_logs:
            return False  # an old or duplicate snapshot must not roll back the game
        board = state.get("mapState") or {}
        corners = {
            k: v
            for k, v in (board.get("tileCornerStates") or {}).items()
            if v.get("owner") != self.corners.get(k, {}).get("owner")
            or v.get("buildingType") != self.corners.get(k, {}).get("buildingType")
        }
        edges = {
            k: v
            for k, v in (board.get("tileEdgeStates") or {}).items()
            if v.get("owner") != self.edges.get(k, {}).get("owner")
            or v.get("type") != self.edges.get(k, {}).get("type")
        }
        diff = {
            "gameLogState": new_logs,
            "mapState": {"tileCornerStates": corners, "tileEdgeStates": edges},
            "currentState": state.get("currentState") or {},
        }
        # Full player snapshots include hidden card placeholders (resource 0)
        # that are not safe to turn into hand checks. Later diffs check cards.
        robber = state.get("mechanicRobberState", {}).get("locationTileIndex")
        if robber != self.robber_tile:
            diff["mechanicRobberState"] = state["mechanicRobberState"]
        self.apply(diff)
        # Port rates from a settlement whose diff was missed (seen live: the
        # opponent then traded 4 wood for 2 ore at 2:1).
        for c, ps in (state.get("playerStates") or {}).items():
            if "bankTradeRatiosState" in ps:
                self.ratios.setdefault(int(c), {}).update(
                    {int(r): n for r, n in ps["bankTradeRatiosState"].items()}
                )
        return True

    # ----- one diff -----
    def apply(self, diff):
        logs = diff.get("gameLogState") or {}
        self.last_log_id = max([self.last_log_id, *(int(k) for k in logs)])
        map_diff = diff.get("mapState", {})
        placed = []  # pieces placed in this diff, waiting to be put in log order
        for idx, c in map_diff.get("tileCornerStates", {}).items():
            self.corners.setdefault(idx, {}).update(c)
            # An upgrade to a city only sends the new building type; the owner
            # is already on the corner.
            owner = self.corners[idx].get("owner")
            if owner and c.get("buildingType") in BUILDING:
                node = _corner_node(self.catan_map, self.corners[idx])
                placed.append((owner, BUILDING[c["buildingType"]], node))
        for idx, e in map_diff.get("tileEdgeStates", {}).items():
            self.edges.setdefault(idx, {}).update(e)
            if "owner" in e and e.get("type") == 1:
                nodes = list(_edge_nodes(self.catan_map, self.edges[idx]))
                placed.append((e["owner"], "road", nodes))
        robber = diff.get("mechanicRobberState", {}).get("locationTileIndex")
        dev_state = diff.get("mechanicDevelopmentCardsState", {}).get("players", {})
        bought = {}  # color -> cards bought this diff, in order

        entries = sorted(
            diff.get("gameLogState", {}).items(), key=lambda kv: int(kv[0])
        )
        texts = [e.get("text", {}) for _, e in entries]
        for position, text in enumerate(texts):
            kind = text.get("type")
            color = text.get("playerColor")
            if (
                self.pending_play is not None
                and kind not in (LOG_MONOPOLY, LOG_YEAR_OF_PLENTY)
                and kind not in LOG_IGNORED
            ):
                self._resolve_unfinished_play()
            if kind in LOG_PIECE_NOTICE:
                # Setup roads and settlements can carry a notice immediately
                # before the ordinary placement log. The placement below
                # accounts for the board change; require that matching log.
                if not any(
                    later.get("type") == LOG_PLACED_SETUP
                    and later.get("playerColor") == color
                    and later.get("pieceEnum") == text.get("pieceEnum")
                    for later in texts[position + 1 :]
                ):
                    raise _Unsupported(f"piece notice without setup placement: {text}")
            elif kind in (LOG_PLACED_SETUP, LOG_BUILT):
                piece = PIECE.get(text.get("pieceEnum"))
                match = self._next_placement(placed, color, piece)
                if match is None:
                    raise _Unsupported(f"log entry {text} has no matching board change")
                placed.remove(match)
                self.emit(color, BUILD_ACTION[piece], match[2])
            elif kind == LOG_ROLLED:
                dice = [text["firstDice"], text["secondDice"]]
                self.emit(color, ActionType.ROLL.value, dice, dice)
            elif kind == LOG_DISCARDED:
                self.discards.extend((color, RESOURCE[c]) for c in text["cardEnums"])
            elif kind == LOG_MOVED_ROBBER:
                if robber is None:
                    raise _Unsupported(
                        f"robber moved without a new robber tile: {text}"
                    )
                # The steal (or "nothing to steal") entry follows, in this
                # update or, live, in a later one. With nobody to rob colonist
                # may log nothing at all: the next other entry settles that.
                self._resolve_robber()
                self.pending_robber = (color, list(self._tile_cube(robber)))
            elif kind in STEALS:
                if self.pending_robber is None:
                    raise _Unsupported(f"steal without a robber move: {text}")
                self._resolve_robber(text)
            elif kind == LOG_BOUGHT_DEV_CARD:
                bought.setdefault(color, 0)
                bought[color] += 1
                self.emit(color, ActionType.BUY_DEVELOPMENT_CARD.value, None, None)
            elif kind == LOG_PLAYED_DEV_CARD:
                card = DEV_CARD.get(text.get("cardEnum"))
                if card == KNIGHT:
                    self.emit(color, ActionType.PLAY_KNIGHT_CARD.value, None)
                elif card == ROAD_BUILDING:
                    self.emit(color, ActionType.PLAY_ROAD_BUILDING.value, None)
                elif card in (MONOPOLY, YEAR_OF_PLENTY):
                    self.pending_play = (color, card)
                else:
                    raise _Unsupported(f"played development card {text}")
            elif kind == LOG_MONOPOLY:
                self._play(
                    color,
                    MONOPOLY,
                    ActionType.PLAY_MONOPOLY.value,
                    RESOURCE[text["cardEnum"]],
                )
            elif kind == LOG_YEAR_OF_PLENTY:
                cards = [RESOURCE[c] for c in text["cardEnums"]]
                self._play(
                    color, YEAR_OF_PLENTY, ActionType.PLAY_YEAR_OF_PLENTY.value, cards
                )
            elif kind == LOG_TRADED_WITH_BANK:
                self._bank_trade(
                    color, text["givenCardEnums"], text["receivedCardEnums"]
                )
            elif kind == LOG_BANK_SHORTAGE:
                # The roll applies bank shortages. A sole claimant receives
                # the remaining cards; multiple claimants receive none.
                distributed = text.get("distributedCount")
                expected = (
                    min(text.get("count", 0), text.get("bankCount", 0))
                    if len(text.get("playerColors", [])) == 1
                    else 0
                )
                if distributed != expected:
                    raise _Unsupported(f"bank shortage with distribution: {text}")
            elif kind == LOG_WON:
                if self.conv.ended != "resignation":
                    self.record["winner"] = self.seat[color]
                    self.conv.ended = "win"
            elif kind == LOG_RESIGNED:
                self.conv.ended = "resignation"
                self.record["resigned"] = self.seat[color]
            elif kind in LOG_IGNORED:
                continue
            else:
                raise _Unsupported(f"log entry type {kind}: {text}")
        if placed:
            raise _Unsupported(f"board changes without a log entry: {placed}")
        # A monopoly or year of plenty may wait for its cards in a later
        # update: colonist's own bots choose them in a separate step (seen
        # live). Anything else logged in between stops the record (above).
        if robber is not None:
            self.robber_tile = robber

        self._fill_in_draws(bought, dev_state)
        self._turns(diff.get("currentState", {}))
        self._player_states(diff.get("playerStates", {}))

    def _play(self, color, card, action_type, value):
        if self.pending_play != (color, card):
            raise _Unsupported(
                f"{card} cards for {color} without the card being played"
            )
        self.pending_play = None
        self.emit(color, action_type, value)

    def _resolve_unfinished_play(self):
        """Something else happened before a played monopoly or year of plenty
        got its cards. colonist's Easy bot has played Monopoly and let its turn
        end without choosing (seen live): the card is spent and nothing moves.
        In 1v1 the victim is the recording player, whose hand is known, so a
        monopoly on a resource they hold none of has exactly that effect."""
        if self.pending_play is None:
            return
        color, card = self.pending_play
        if card == MONOPOLY and color != self.you:
            for r in sorted(RESOURCE):
                if r not in self.your_cards:
                    self.pending_play = None
                    self.log.append(
                        [
                            [
                                self.seat[color],
                                ActionType.PLAY_MONOPOLY.value,
                                RESOURCE[r],
                            ],
                            None,
                        ]
                    )
                    return
        raise _Unsupported(f"{self.pending_play} played, but its cards never came")

    def _bank_trade(self, color, given, received):
        """One colonist entry can hold several trades: split the cards given
        into lots at the player's rate for each resource, one per card received."""
        rates = dict(self.ratios.get(color, {}))
        if (
            len(set(given)) == 1
            and received
            and len(given) % len(received) == 0
            and 2 <= len(given) // len(received) <= 4
        ):
            # One resource for several cards: the trade itself shows the rate.
            # The player's new rate can arrive in the same update, after the
            # log (seen live: 4 wood for 2 ore at a 2:1 port, rate still 4).
            # The replay checks the port, so a wrong rate can't slip through.
            rates[given[0]] = len(given) // len(received)
        lots = []
        for r in sorted(set(given), key=given.index):
            n, rate = given.count(r), rates.get(r, 4)
            if n % rate:
                raise _Unsupported(f"trade of {n} x {r} at {rate}:1")
            lots += [r] * (n // rate)
        if len(lots) != len(received):
            raise _Unsupported(f"trade gives {given} for {received}")
        for r, want in zip(lots, received):
            rate = rates.get(r, 4)
            value = [RESOURCE[r]] * rate + [None] * (4 - rate) + [RESOURCE[want]]
            self.emit(color, ActionType.MARITIME_TRADE.value, value)

    def _fill_in_draws(self, bought, dev_state):
        """Development cards bought in this diff: known to the buyer only."""
        for color, n in bought.items():
            seen = dev_state.get(str(color), {}).get("developmentCardsBoughtThisTurn")
            cards = seen[-n:] if seen and len(seen) >= n else [HIDDEN_CARD] * n
            buys = [
                e
                for e in self.log
                if e[0][0] == self.seat[color]
                and e[0][1] == ActionType.BUY_DEVELOPMENT_CARD.value
            ]
            for entry, c in zip(buys[-n:], cards):
                if c != HIDDEN_CARD:
                    entry[0][2] = entry[1] = DEV_CARD[c]

    def _turns(self, cur):
        if "completedTurns" in cur:
            if (
                self.completed >= self.setup_turns
                and cur["completedTurns"] > self.completed
            ):
                self.emit(self.current, ActionType.END_TURN.value, None)
            self.completed = cur["completedTurns"]
        if "currentTurnPlayerColor" in cur:
            self.current = cur["currentTurnPlayerColor"]

    def _player_states(self, player_states):
        self._flush_discards()
        for c, ps in player_states.items():
            color = int(c)
            if "bankTradeRatiosState" in ps:
                self.ratios.setdefault(color, {}).update(
                    {int(r): n for r, n in ps["bankTradeRatiosState"].items()}
                )
            check = {"at": len(self.log), "color": self.seat[color]}
            if "victoryPointsState" in ps:
                self.points.setdefault(color, {}).update(ps["victoryPointsState"])
                p = {int(k): v for k, v in self.points[color].items()}
                # Visible points: everything but victory point cards.
                check["visible_vps"] = (
                    p.get(0, 0) + 2 * p.get(1, 0) + 2 * p.get(3, 0) + 2 * p.get(4, 0)
                )
            cards = ps.get("resourceCards", {}).get("cards")
            if cards is not None and color == self.you:
                self.your_cards = list(cards)
            # A steal may be counted before its log entry arrives.
            if cards is not None and self.pending_robber is None:
                check["resources"] = len(cards)
                if color == self.you:
                    hand = {r: 0 for r in RESOURCE.values()}
                    for card in cards:
                        hand[RESOURCE[card]] += 1
                    check["hand"] = hand
            if len(check) > 2:
                self.record["checks"].append(check)

    def finish(self, end_game_state):
        """Hidden victory point cards, revealed in colonist's end-of-game summary.

        Also a forfeit: when a player leaves (or times out) colonist logs the
        other as the winner without a resignation entry, short of the points
        to win. That is recorded as a resignation by the one who left, so the
        replay doesn't expect a winner on points."""
        if not end_game_state:
            return
        hidden, points = {}, {}
        for c, p in end_game_state.get("players", {}).items():
            color = int(c)
            if color not in self.seat:
                continue
            vps = p.get("victoryPoints", {})
            points[self.seat[color]] = sum(
                END_GAME_POINTS.get(k, 1) * n for k, n in vps.items()
            )
            if color != self.you:
                hidden[self.seat[color]] = vps.get("2", 0)
        self.record["hidden_vp_cards"] = hidden
        winner = self.record.get("winner")
        if (
            self.conv.ended == "win"
            and len(points) == 2
            and winner in points
            and points[winner] < self.record["rules"]["vps_to_win"]
        ):
            self.conv.ended = "resignation"
            self.record["resigned"] = next(c for c in points if c != winner)
            self.record["forfeit"] = True
            del self.record["winner"]


def game_start(frames):
    """The first full game snapshot. Type 4 also comes with another layout, a
    list of players (seen live before a ranked game), which is not a start."""
    for f in frames:
        if f.dir == "in" and f.type == GAME_STATE:
            payload = f.data["data"].get("payload")
            if isinstance(payload, dict) and "gameState" in payload:
                return f
    return None


def convert(frames) -> Conversion:
    start = game_start(frames)
    if start is None:
        raise ConvertError(
            "capture has no game start; the extension must be on before the game begins"
        )
    c = _Converter(start.data["data"]["payload"], start)
    game_id = (start.data["data"]["payload"].get("gameSettings") or {}).get("id")
    try:
        for f in frames:
            if f.dir != "in" or f.seq < start.seq:
                continue
            if f.type == DIFF:
                c.apply(f.data["data"]["payload"].get("diff", {}))
            elif f.type == GAME_STATE and f.seq > start.seq:
                payload = f.data["data"].get("payload")
                if (
                    isinstance(payload, dict)
                    and "gameState" in payload
                    and (payload.get("gameSettings") or {}).get("id") == game_id
                ):
                    c.reconcile(payload["gameState"])
            elif f.type == GAME_OVER:
                c.finish(f.data["data"]["payload"].get("endGameState"))
            if c.conv.ended in ("win", "resignation") and f.type == GAME_OVER:
                break
    except _Unsupported as e:
        c.conv.unsupported.append(str(e))
        c.conv.ended = "unsupported"
        # The record says so, so the replay check doesn't count it as clean.
        c.record["incomplete"] = str(e)
    return c.conv


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
