"""Phase 4: following a colonist game live, and the bridge to the extension."""

import asyncio
import json
import os

import pytest

from catanatron.colonist import convert as C
from catanatron.colonist.bridge import Bridge, serve_forever
from catanatron.colonist.capture import load
from catanatron.colonist.live import LiveGame
from catanatron.colonist.net import random_net

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
GAMES = ["capture_full_game", "capture_short_game", "capture_road_building"]


def _messages(name):
    with open(os.path.join(FIXTURES, name + ".json")) as f:
        doc = json.load(f)
    return [
        {"kind": "frame", "dir": m["dir"], "b64": m["b64"], "t": m["t"]}
        for m in doc["messages"]
        if "b64" in m and m["dir"] in ("in", "out")
    ]


@pytest.mark.parametrize("name", GAMES)
def test_follows_real_games_without_losing_sync(name):
    frames = load(os.path.join(FIXTURES, name + ".json"))
    start = next(f for f in frames if f.dir == "in" and f.type == C.GAME_STATE)
    live = LiveGame(start.data["data"]["payload"])
    assert live.bot is not None
    decisions = 0
    for f in frames:
        if f.seq <= start.seq:
            continue
        live.apply(f)
        assert live.error is None
        if live.bot_to_move:
            decisions += 1
            for action in live.game.playable_actions:
                described = live.describe(action)
                assert described["type"] == action.action_type.value
                assert described["label"]
    assert decisions > 0


def test_reports_out_of_sync_and_stops():
    frames = load(os.path.join(FIXTURES, "capture_short_game.json"))
    start = next(f for f in frames if f.dir == "in" and f.type == C.GAME_STATE)
    live = LiveGame(start.data["data"]["payload"])
    # colonist "says" the bot holds 99 cards: the simulator can't agree.
    live.record["checks"].append({"at": 0, "color": live.bot.value, "resources": 99})
    diff = next(f for f in frames if f.seq > start.seq and f.type == C.DIFF)
    live.apply(diff)
    assert live.error and "99" in live.error
    assert not live.bot_to_move


def test_bridge_suggests_legal_moves_only_on_the_bots_turn():
    bridge = Bridge(random_net(hidden=(16, 16)), simulations=4)
    bridge.reset()
    moves, statuses = [], []
    for message in _messages("capture_full_game"):
        for reply in bridge.handle(message):
            (moves if reply["kind"] == "move" else statuses).append(reply)
            if reply["kind"] == "move":
                # The move is one of the bot's legal moves at that moment.
                legal = [
                    bridge.live.describe(a) for a in bridge.live.game.playable_actions
                ]
                assert reply["move"] in legal
                assert 0 <= reply["win_chance"] <= 1
                assert reply["options"][0]["move"] == reply["move"]
    assert [s["state"] for s in statuses] == [
        "following",
        "waiting",
    ]  # start, game over
    assert len(moves) > 50
    assert len({m["position"] for m in moves}) == len(
        moves
    )  # never twice for one position
    discards = [m for m in moves if m["move"]["type"] == "DISCARD_RESOURCE"]
    assert discards and all(len(m["plan"]) > 1 for m in discards)


def test_bridge_server_end_to_end():
    from websockets.asyncio.client import connect

    async def run():
        server = asyncio.create_task(
            serve_forever(lambda: Bridge(random_net(hidden=(16,)), 2), port=18765)
        )
        await asyncio.sleep(0.3)
        got = []
        async with connect("ws://127.0.0.1:18765") as ws:
            got.append(json.loads(await ws.recv()))  # "waiting"
            for message in _messages("capture_short_game"):
                await ws.send(json.dumps(message))
            while True:
                try:
                    got.append(json.loads(await asyncio.wait_for(ws.recv(), 2)))
                except asyncio.TimeoutError:
                    break
        server.cancel()
        return got

    got = asyncio.run(run())
    assert got[0]["state"] == "waiting"
    assert any(g["kind"] == "status" and g["state"] == "following" for g in got)
    assert any(g["kind"] == "move" for g in got)
