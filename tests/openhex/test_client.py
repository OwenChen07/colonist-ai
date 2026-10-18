"""The client plays whole games against a fake OpenHex server: a simulator
game behind the API, the opponent a random player, events as test_follow
tells them. Every action the client sends must be legal there."""

import random

import pytest

from catanatron.colonist.net import random_net
from catanatron.colonist.rules import new_game
from catanatron.models.enums import RESOURCES, ActionType
from catanatron.models.player import Color, RandomPlayer
from catanatron.openhex import board as B
from catanatron.openhex import client as C
from catanatron.openhex.follow import color
from catanatron.state_functions import player_key

from tests.openhex.test_board import land, ports
from tests.openhex.test_follow import events_of

A = ActionType


class FakeServer:
    def __init__(self, seed, me):
        self.rng = random.Random(seed)
        self.game = new_game(
            [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed
        )
        m = self.game.state.board.map
        self.board = B.Board(land(m), ports(m, B.Board(land(m), [])))
        self.tiles, self.ports = land(m), ports(m, self.board)
        self.desert = next(c for c, t in m.land_tiles.items() if t.resource is None)
        self.me = me
        self.sent = []
        self.limit = 600  # action records before the game "times out"
        self.opponent_moves()

    @property
    def over(self):
        g = self.game
        return g.winning_color() is not None or len(g.state.action_records) > self.limit

    def opponent_moves(self):
        g = self.game
        while not self.over and g.state.current_color() != color(self.me):
            g.execute(self.rng.choice(g.playable_actions))

    def state(self):
        g, s = self.game, self.game.state
        players = []
        for c in s.colors:
            k = player_key(s, c)
            hand = {
                B.RESOURCE_NAME[r]: s.player_state[f"{k}_{r}_IN_HAND"]
                for r in RESOURCES
            }
            players.append({"color": c.value.lower(), "hand": hand})
        pending = None
        if s.is_discarding:
            n = s.discard_counts[s.color_to_index[color(self.me)]]
            pending = {"type": "discard", "players": {self.me: n}}
        elif s.is_moving_knight:
            pending = {"type": "moveRobber"}
        winner = g.winning_color()
        return {
            "game": {
                "id": "fake",
                "status": "completed" if self.over else "active",
                "current_player_color": s.current_color().value.lower(),
                "winner": winner.value.lower() if winner else None,
                "players": [{"color": c.value.lower()} for c in s.colors],
            },
            "state": {
                "board": {
                    "tiles": self.tiles,
                    "ports": self.ports,
                    "robberHex": B.xyz(self.desert),
                },
                "turnOrder": [c.value.lower() for c in s.colors],
                "isSetupPhase": s.is_initial_build_phase,
                "players": players,
                "pending": pending,
            },
            "your_color": self.me,
        }

    def call(self, method, path, body=None, tries=1):
        if path.endswith("/state"):
            return self.state()
        if "/events?after=" in path:
            after = int(path.split("=")[1])
            events = events_of(self.game, self.board, color(self.me))
            return {"events": [e for e in events if e["seq"] > after]}
        assert method == "POST" and path.endswith("/actions"), path
        self.sent.append(body["action"])
        self.execute(body["action"])
        self.opponent_moves()
        return {}

    def pick(self, test):
        found = [a for a in self.game.playable_actions if test(a)]
        if not found:
            s = self.game.state
            raise C.Rejected(
                f"illegal: {self.sent[-1]}; {s.current_color()} "
                f"{s.current_prompt} {self.game.playable_actions[:6]}"
            )
        # A robber move steals when it can, as OpenHex's steal would.
        found.sort(key=lambda a: a.action_type == A.MOVE_ROBBER and a.value[1] is None)
        self.game.execute(found[0])

    def execute(self, a):
        t, b = a["type"], self.board
        is_type = lambda kind: (lambda x: x.action_type == kind)
        if t in ("placeInitialSettlement", "buildSettlement"):
            node = b.node(a["vertex"])
            self.pick(lambda x: x.action_type == A.BUILD_SETTLEMENT and x.value == node)
        elif t == "buildCity":
            node = b.node(a["vertex"])
            self.pick(lambda x: x.action_type == A.BUILD_CITY and x.value == node)
        elif t in ("placeInitialRoad", "buildRoad"):
            edge = frozenset(b.edge(a["edge"]))
            self.pick(
                lambda x: x.action_type == A.BUILD_ROAD and frozenset(x.value) == edge
            )
        elif t == "rollDice":
            self.pick(is_type(A.ROLL))
        elif t == "endTurn":
            self.pick(is_type(A.END_TURN))
        elif t == "buyDevCard":
            self.pick(is_type(A.BUY_DEVELOPMENT_CARD))
        elif t == "playRoadBuilding":
            self.pick(is_type(A.PLAY_ROAD_BUILDING))
        elif t == "playMonopoly":
            r = B.RESOURCE[a["resource"]]
            self.pick(lambda x: x.action_type == A.PLAY_MONOPOLY and x.value == r)
        elif t == "playYearOfPlenty":
            rs = sorted(B.RESOURCE[r] for r in a["resources"])
            self.pick(
                lambda x: x.action_type == A.PLAY_YEAR_OF_PLENTY
                and sorted(x.value) == rs
            )
        elif t in ("playKnight", "moveRobber"):
            if t == "playKnight":
                self.pick(is_type(A.PLAY_KNIGHT_CARD))
            h = B.as_hex(a["hex"])
            self.pick(lambda x: x.action_type == A.MOVE_ROBBER and x.value[0] == h)
        elif t == "discardResources":
            for name, n in sorted(a["resources"].items()):
                r = B.RESOURCE[name]
                for _ in range(n):
                    self.pick(
                        lambda x: x.action_type == A.DISCARD_RESOURCE and x.value == r
                    )
            if self.game.state.is_discarding and (
                self.game.state.current_color() == color(self.me)
            ):
                raise AssertionError(f"too few cards discarded: {a}")
        elif t == "tradeWithBank":
            ((give, n),) = a["offering"].items()
            ((get, m),) = a["requesting"].items()
            offer = tuple([B.RESOURCE[give]] * n + [None] * (4 - n) + [B.RESOURCE[get]])
            self.pick(
                lambda x: x.action_type == A.MARITIME_TRADE and tuple(x.value) == offer
            )
        else:
            raise C.Rejected(f"unknown action {t}")


@pytest.mark.parametrize("seed,me", [(0, "red"), (1, "blue")])
def test_client_plays_a_whole_game(seed, me, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "GAMES_DIR", str(tmp_path))
    monkeypatch.setattr(C.time, "sleep", lambda s: None)

    def no_fallback(self, state):
        raise AssertionError(self.record["notes"][-1])

    monkeypatch.setattr(C.Player, "fallback", no_fallback)
    server = FakeServer(seed, me)
    player = C.Player(server, "fake", random_net((32,)), 4, random.Random(seed))
    info = player.play()
    assert info["status"] == "completed"
    assert not player.broken, player.record["notes"]
    assert not any(m[0] == "fallback" for m in player.moves)
    kinds = {a["type"] for a in server.sent}
    assert {"placeInitialSettlement", "placeInitialRoad", "rollDice"} <= kinds
    assert (tmp_path / "fake.json").exists()
