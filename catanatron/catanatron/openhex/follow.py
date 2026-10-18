"""Follow an OpenHex game in the simulator, and say our moves in OpenHex's terms.

Every OpenHex event becomes zero or more log entries in the record format
(colonist/record.py: [[color, action type, value], result]); the position is
the log replayed from a fresh game on the game's board, as live.py does for
colonist. A replay takes every roll, steal and card from the log, and the
opponent's hidden card draws are filled in by resolve_hidden_draws. In 1v1
every steal involves us, so the opponent's hand is fully known.
"""

from catanatron.colonist.record import resolve_hidden_draws
from catanatron.colonist.replay import RecordedPlayer, decision_of
from catanatron.colonist.rules import COLONIST_1V1
from catanatron.game import Game
from catanatron.models.actions import generate_playable_actions
from catanatron.models.enums import (
    KNIGHT,
    MONOPOLY,
    ROAD_BUILDING,
    VICTORY_POINT,
    YEAR_OF_PLENTY,
    ActionRecord,
    ActionType,
)
from catanatron.models.player import Color
from catanatron.openhex.board import RESOURCE, RESOURCE_NAME, as_hex, xyz
from catanatron.serialization import _tupled, action_from_json

CARD = {
    "knight": KNIGHT,
    "victoryPoint": VICTORY_POINT,
    "monopoly": MONOPOLY,
    "roadBuilding": ROAD_BUILDING,
    "yearOfPlenty": YEAR_OF_PLENTY,
}
#: Events the replay derives by itself (it checks them instead).
DERIVED = {
    "setupResourcesCollected",
    "setupResourcesGranted",
    "resourcesProduced",
    "longestRoadClaimed",
    "largestArmyClaimed",
    "longestRoadAwarded",
    "largestArmyAwarded",
    "victoryPointRevealed",
    "gameWon",
}

A = ActionType


class OutOfSync(Exception):
    """The simulator can't follow OpenHex; the bot must not trust its position."""


def color(name):
    return Color[name.upper()]


def openhex_color(c):
    return c.value.lower()


class Follower:
    def __init__(self, board, turn_order, me, robber_hex=None):
        """`board`: board.Board; `turn_order`: OpenHex colors; `me`: ours."""
        self.board = board
        self.seats = [color(c) for c in turn_order]
        self.me = color(me)
        self.robber = as_hex(robber_hex) if robber_hex is not None else None
        self.log = []
        self.seq = -1  # highest event seq applied
        self.pending_robber = None  # (mover, hex) waiting for its steal event
        self.over = False
        self.game = None
        self.unknown = set()  # event types skipped

    # ----- events -> log -----
    def feed(self, events):
        """OpenHex events ({seq, event}) in seq order; old ones are skipped."""
        for item in events:
            if item["seq"] <= self.seq:
                continue
            self.seq = item["seq"]
            self._event(item["event"])

    def _add(self, who, kind, value=None, result=None):
        self.log.append([[who.value, kind.value, value], result])

    def _flush_robber(self, victim=None, resource=None):
        mover, h = self.pending_robber
        self.pending_robber = None
        self._add(
            mover,
            A.MOVE_ROBBER,
            [list(h), victim.value if victim else None],
            RESOURCE[resource] if resource else None,
        )

    def _event(self, e):
        t = e["type"]
        if t == "robberMoved" and self.pending_robber == (
            color(e["player"]),
            as_hex(e["hex"]),
        ):
            return  # the knight already said where the robber goes
        if self.pending_robber and t != "resourceStolen":
            self._flush_robber()
        if t in DERIVED:
            self.over = self.over or t == "gameWon"
            return
        who = color(e["player"])
        b = self.board
        # The site's own replayer names setup and free road placements apart
        # (setupSettlementPlaced, setupRoadPlaced, freeRoadPlaced) and may give
        # a road as its two vertices; its guide lists only the plain names.
        if t in ("settlementBuilt", "setupSettlementPlaced"):
            self._add(who, A.BUILD_SETTLEMENT, b.node(e["vertex"]))
        elif t == "cityBuilt":
            self._add(who, A.BUILD_CITY, b.node(e["vertex"]))
        elif t in ("roadBuilt", "setupRoadPlaced", "freeRoadPlaced"):
            where = e["edge"] if e.get("edge") is not None else e["vertices"]
            self._add(who, A.BUILD_ROAD, list(b.edge(where)))
        elif t == "diceRolled":
            dice = [e["die1"], e["die2"]] if "die1" in e else list(e["dice"])
            self._add(who, A.ROLL, None, dice)
        elif t == "devCardBought":
            card = CARD[e["card"]] if e.get("card") else None
            self._add(who, A.BUY_DEVELOPMENT_CARD, None, card)
        elif t == "knightPlayed":
            self._add(who, A.PLAY_KNIGHT_CARD)
            if e.get("hex") is not None:  # the guide lists it among the fields
                self.pending_robber = (who, as_hex(e["hex"]))
        elif t == "roadBuildingPlayed":
            self._add(who, A.PLAY_ROAD_BUILDING)
        elif t == "monopolyPlayed":
            self._add(who, A.PLAY_MONOPOLY, RESOURCE[e["resource"]])
        elif t == "yearOfPlentyPlayed":
            self._add(who, A.PLAY_YEAR_OF_PLENTY, [RESOURCE[r] for r in e["resources"]])
        elif t == "robberMoved":
            self.pending_robber = (who, as_hex(e["hex"]))
        elif t == "resourceStolen":
            if not e.get("resource"):
                raise OutOfSync(f"a steal we can't see: {e}")
            if self.pending_robber:
                self._flush_robber(color(e["target"]), e["resource"])
                return
            # settle() already wrote the robber move as stealing nothing (the
            # steal came after a poll): give that move its steal.
            last = self.log[-1][0] if self.log else None
            if not (
                last
                and last[0] == who.value
                and last[1] == A.MOVE_ROBBER.value
                and last[2][1] is None
            ):
                raise OutOfSync(f"steal without a robber move: {e}")
            last[2][1] = color(e["target"]).value
            self.log[-1][1] = RESOURCE[e["resource"]]
        elif t == "resourcesDiscarded":
            for r, n in sorted(e["resources"].items()):
                for _ in range(n):
                    self._add(who, A.DISCARD_RESOURCE, RESOURCE[r], RESOURCE[r])
        elif t == "bankTradeExecuted":
            ((give, n),) = e["offering"].items()
            ((get, m),) = e["requesting"].items()
            if m != 1 or n not in (2, 3, 4):
                raise OutOfSync(f"a bank trade the simulator can't make: {e}")
            offer = [RESOURCE[give]] * n + [None] * (4 - n) + [RESOURCE[get]]
            self._add(who, A.MARITIME_TRADE, offer)
        elif t == "turnEnded":
            self._add(who, A.END_TURN)
        else:
            # The site has more event names than its guide lists. Skip one we
            # don't know; if it mattered, the replay's legality check says so.
            self.unknown.add(t)

    def settle(self):
        """No steal is coming (OpenHex has no steal pending): a robber move
        still waiting for one stole nothing."""
        if self.pending_robber:
            self._flush_robber()

    # ----- log -> position -----
    def new_game(self):
        game = Game(
            [RecordedPlayer(c) for c in self.seats],
            seed=0,
            catan_map=self.board.catan_map,
            **COLONIST_1V1,
        )
        s = game.state
        by_color = {p.color: p for p in s.players}
        s.players = [by_color[c] for c in self.seats]
        s.colors = tuple(self.seats)
        s.color_to_index = {c: i for i, c in enumerate(self.seats)}
        s.random = None
        if self.robber is not None:
            s.board.robber_coordinate = self.robber
        game.playable_actions = generate_playable_actions(s)
        return game

    def replay(self):
        """The position after the log, as a Game (also kept in self.game)."""
        log = resolve_hidden_draws({"log": self.log})
        game = self.new_game()
        deferred = []  # discards OpenHex reported before the earlier seat's
        for i, (payload, result) in enumerate(log):
            action = action_from_json(payload)
            if not self._apply(game, action, result):
                if action.action_type == A.DISCARD_RESOURCE:
                    deferred.append((action, result))
                    continue
                raise OutOfSync(f"log entry {i} {payload} isn't legal in the simulator")
            while deferred and self._apply(game, *deferred[0]):
                deferred.pop(0)
        self.deferred = deferred
        self.game = game
        return game

    @staticmethod
    def _apply(game, action, result):
        decision = decision_of(action, game.playable_actions)
        if decision not in game.playable_actions:
            return False
        game.execute(
            decision,
            validate_action=False,
            action_record=ActionRecord(action, _tupled(result)),
        )
        return True


# ----- our move -> OpenHex actions -----
def resources(counts):
    out = {}
    for r in counts:
        out[RESOURCE_NAME[r]] = out.get(RESOURCE_NAME[r], 0) + 1
    return out


def to_openhex(board, game, action):
    """One simulator action as an OpenHex action dict. Knights and robber moves
    are special (see Client): a knight carries the robber's hex, and a robber
    move's steal is a second action."""
    t, v = action.action_type, action.value
    setup = game.state.is_initial_build_phase
    if t == A.BUILD_SETTLEMENT:
        kind = "placeInitialSettlement" if setup else "buildSettlement"
        return {"type": kind, "vertex": board.vertex(v)}
    if t == A.BUILD_CITY:
        return {"type": "buildCity", "vertex": board.vertex(v)}
    if t == A.BUILD_ROAD:
        kind = "placeInitialRoad" if setup else "buildRoad"
        return {"type": kind, "edge": board.road(v)}
    if t == A.ROLL:
        return {"type": "rollDice"}
    if t == A.END_TURN:
        return {"type": "endTurn"}
    if t == A.BUY_DEVELOPMENT_CARD:
        return {"type": "buyDevCard"}
    if t == A.PLAY_ROAD_BUILDING:
        return {"type": "playRoadBuilding"}
    if t == A.PLAY_YEAR_OF_PLENTY:
        names = [RESOURCE_NAME[r] for r in v]
        return {"type": "playYearOfPlenty", "resources": names}
    if t == A.PLAY_MONOPOLY:
        return {"type": "playMonopoly", "resource": RESOURCE_NAME[v]}
    if t == A.MARITIME_TRADE:
        give = [r for r in v[:4] if r]
        return {
            "type": "tradeWithBank",
            "offering": resources(give),
            "requesting": resources([v[4]]),
        }
    if t == A.MOVE_ROBBER:
        return {"type": "moveRobber", "hex": xyz(v[0])}
    raise ValueError(f"no OpenHex action for {action}")
