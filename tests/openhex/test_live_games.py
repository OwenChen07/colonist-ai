"""Real games from the live server (tests/openhex/games/, trimmed game files):
their events must replay through the Follower to the server's own end
position. 86d4348b is a win with two monopolies, a9a34aca a loss with road
building, year of plenty, discards and bank trades, 389a42ca was aborted on
a Road Building card (the server wanted placeFreeRoad)."""

import glob
import json
import os

import pytest

from catanatron.openhex.board import Board
from catanatron.openhex.follow import Follower, color

RES = ["wood", "brick", "sheep", "wheat", "ore"]
GAMES = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "games", "*.json")))


def test_fixtures_are_there():
    assert len(GAMES) >= 3


@pytest.mark.parametrize("path", GAMES, ids=os.path.basename)
def test_live_game_replays_to_the_servers_end(path):
    with open(path) as f:
        g = json.load(f)
    board = Board(g["tiles"], g["ports"])
    follower = Follower(board, g["turn_order"], g["me"], g["robber"])
    # in two polls, the second one overlapping the first
    half = len(g["events"]) // 2
    follower.feed(g["events"][: half + 1])
    follower.feed(g["events"][half - 5 :])
    follower.settle()
    s = follower.replay().state
    assert follower.unknown == set()
    assert follower.deferred == []
    ps = s.player_state
    for p in g["final"]["players"]:
        i = s.color_to_index[color(p["color"])]
        hand = {r: ps[f"P{i}_{r.upper()}_IN_HAND"] for r in RES}
        if "handSize" in p:  # theirs: hand {} and its size
            assert sum(hand.values()) == p["handSize"]
        else:  # ours: every card known
            assert hand == {r: p.get("hand", {}).get(r, 0) for r in RES}
        assert ps[f"P{i}_PLAYED_KNIGHT"] == p["knightsPlayed"]
        assert ps[f"P{i}_LONGEST_ROAD_LENGTH"] == p["longestRoadLength"]
    winner = g["final"]["winner"]
    if winner == g["me"]:  # their hidden point cards can't be counted
        assert ps[f"P{s.color_to_index[color(winner)]}_ACTUAL_VICTORY_POINTS"] >= 15
