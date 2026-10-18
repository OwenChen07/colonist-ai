"""Whole games played in the simulator, told to the Follower as OpenHex events
(as we would see them: the opponent's card draws hidden), must replay to the
same position."""

from catanatron.colonist.rules import new_game
from catanatron.models.enums import RESOURCES, ActionType
from catanatron.models.player import Color, RandomPlayer
from catanatron.openhex import board as B
from catanatron.openhex.follow import CARD, Follower, to_openhex
from catanatron.state_functions import player_key

from tests.openhex.test_board import land, ports

A = ActionType
CARD_NAME = {v: k for k, v in CARD.items()}


def events_of(game, board, me):
    """The game's action records as OpenHex events, seen by `me`. Vertices
    and edges alternate between index and cube forms."""
    out = []

    def add(e):
        out.append({"seq": len(out), "event": e})

    # The site names the 2 x 2 setup placements apart, as its replayer does.
    setup = {A.BUILD_SETTLEMENT: 4, A.BUILD_ROAD: 4}
    for i, record in enumerate(game.state.action_records):
        a, result = record.action, record.result
        who = a.color.value.lower()
        idx = i % 2 == 0
        first = setup.get(a.action_type, 0) > 0
        if first:
            setup[a.action_type] -= 1
        if a.action_type in (A.BUILD_SETTLEMENT, A.BUILD_CITY):
            v = board.vertex_of_node[a.value]
            add(
                {
                    "type": (
                        "cityBuilt"
                        if a.action_type == A.BUILD_CITY
                        else "setupSettlementPlaced" if first else "settlementBuilt"
                    ),
                    "player": who,
                    "vertex": B.VERTICES.index(v) if idx else B.xyz(v),
                }
            )
        elif a.action_type == A.BUILD_ROAD:
            pair = board.edge_of_nodes[frozenset(a.value)]
            t = "setupRoadPlaced" if first else "roadBuilt"
            if i % 3 == 0:  # the replayer's form: the two vertices
                add({"type": t, "player": who, "vertices": [B.xyz(p) for p in pair]})
                continue
            edge = B.EDGES.index(pair) if idx else [B.xyz(p) for p in pair]
            add({"type": t, "player": who, "edge": edge})
        elif a.action_type == A.ROLL:
            d1, d2 = result
            add({"type": "diceRolled", "player": who, "die1": d1, "die2": d2})
            add({"type": "resourcesProduced", "production": {}})
        elif a.action_type == A.BUY_DEVELOPMENT_CARD:
            e = {"type": "devCardBought", "player": who}
            if a.color == me:
                e["card"] = CARD_NAME[result]
            add(e)
        elif a.action_type == A.PLAY_KNIGHT_CARD:
            add({"type": "knightPlayed", "player": who})
        elif a.action_type == A.PLAY_ROAD_BUILDING:
            add({"type": "roadBuildingPlayed", "player": who})
        elif a.action_type == A.PLAY_MONOPOLY:
            add(
                {
                    "type": "monopolyPlayed",
                    "player": who,
                    "resource": B.RESOURCE_NAME[a.value],
                }
            )
        elif a.action_type == A.PLAY_YEAR_OF_PLENTY:
            names = [B.RESOURCE_NAME[r] for r in a.value]
            add({"type": "yearOfPlentyPlayed", "player": who, "resources": names})
        elif a.action_type == A.MOVE_ROBBER:
            coordinate, victim = a.value
            add({"type": "robberMoved", "player": who, "hex": B.xyz(coordinate)})
            if victim is not None:
                add(
                    {
                        "type": "resourceStolen",
                        "player": who,
                        "target": victim.value.lower(),
                        "resource": B.RESOURCE_NAME[result],
                    }
                )
        elif a.action_type == A.DISCARD_RESOURCE:
            name = B.RESOURCE_NAME[result]
            last = out[-1]["event"] if out else {}
            if last.get("type") == "resourcesDiscarded" and last["player"] == who:
                last["resources"][name] = last["resources"].get(name, 0) + 1
            else:
                add(
                    {
                        "type": "resourcesDiscarded",
                        "player": who,
                        "resources": {name: 1},
                    }
                )
        elif a.action_type == A.MARITIME_TRADE:
            action = to_openhex(board, game, a)
            add(
                {
                    "type": "bankTradeExecuted",
                    "player": who,
                    "offering": action["offering"],
                    "requesting": action["requesting"],
                }
            )
        elif a.action_type == A.END_TURN:
            add({"type": "turnEnded", "player": who})
        else:
            raise AssertionError(f"no event for {a}")
    return out


def test_followed_games_match():
    seen = set()
    for seed in range(3):
        game = new_game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=seed)
        game.play()  # random play rarely reaches 15 points: the turn limit ends it
        seen |= {r.action.action_type for r in game.state.action_records}
        m = game.state.board.map
        board = B.Board(land(m), ports(m, B.Board(land(m), [])))
        me = game.state.colors[seed % 2]
        desert = next(c for c, t in m.land_tiles.items() if t.resource is None)
        f = Follower(
            board,
            [c.value.lower() for c in game.state.colors],
            me.value.lower(),
            B.xyz(desert),
        )
        events = events_of(game, board, me)
        half = len(events) // 2
        f.feed(events[:half])
        f.feed(events)  # repeats are skipped
        replayed = f.replay()
        s, r = game.state, replayed.state
        assert r.board.buildings == s.board.buildings
        assert set(map(frozenset, r.board.roads)) == set(map(frozenset, s.board.roads))
        assert r.board.robber_coordinate == s.board.robber_coordinate
        for c in s.colors:
            for res in RESOURCES:
                k = f"{player_key(s, c)}_{res}_IN_HAND"
                assert r.player_state[f"{player_key(r, c)}_{res}_IN_HAND"] == (
                    s.player_state[k]
                )
        assert len(r.action_records) == len(s.action_records)
        assert replayed.winning_color() == game.winning_color()
    # the games covered every kind of move OpenHex has
    assert seen >= {
        A.BUILD_CITY,
        A.BUY_DEVELOPMENT_CARD,
        A.DISCARD_RESOURCE,
        A.MARITIME_TRADE,
        A.MOVE_ROBBER,
        A.PLAY_KNIGHT_CARD,
    }
