"""Phase 4: following a colonist game live, and the bridge to the extension."""

import asyncio
import base64
import copy
import json
import os
import time
from types import SimpleNamespace

import pytest

from catanatron.colonist import convert as C
from catanatron.colonist.bridge import (
    ACK_GIVE_UP_SECONDS,
    ACK_SECONDS,
    MAX_RECONNECT_RETRIES,
    Bridge,
    serve_forever,
)
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


def test_ignores_transient_wrong_players_first_setup_placement():
    """A reconnect can briefly assign log 2 to the wrong opening player."""
    import msgpack
    from catanatron.colonist.capture import decode

    messages = _messages("capture_full_game")
    start = next(
        i
        for i, message in enumerate(messages)
        if (frame := decode({**message, "seq": i, "socket": 1})).type == C.GAME_STATE
        and "gameState" in frame.data["data"].get("payload", {})
    )
    first = next(
        i
        for i in range(start + 1, len(messages))
        if decode({**messages[i], "seq": i, "socket": 1}).type == C.DIFF
    )
    bridge = Bridge(random_net(hidden=(8,)), simulations=2)
    bridge.reset()
    bridge.handle(messages[start])
    expected = bridge.live.conv.current
    wrong = next(color for color in bridge.live.conv.play_order if color != expected)
    wire = msgpack.unpackb(base64.b64decode(messages[first]["b64"]), raw=False)
    entry = next(
        entry
        for entry in wire["data"]["payload"]["diff"]["gameLogState"].values()
        if entry["text"]["type"] == C.LOG_PLACED_SETUP
    )
    entry["text"]["playerColor"] = wrong
    transient = {
        **messages[first],
        "b64": base64.b64encode(msgpack.packb(wire)).decode(),
    }

    replies = bridge.handle(transient)
    assert replies[-1]["state"] == "following"
    assert bridge.live.error is None
    assert bridge.live.record["log"] == []
    wire["data"]["payload"]["diff"]["currentState"][
        "currentTurnPlayerColor"
    ] = 4  # A reconnect also delivered turns from a different game.
    foreign = {**messages[first], "b64": base64.b64encode(msgpack.packb(wire)).decode()}
    assert bridge.handle(foreign)[-1]["state"] == "following"
    assert bridge.live.record["log"] == []
    bridge.handle(messages[first])
    assert bridge.live.error is None
    assert bridge.live.record["log"][0][0][0] == bridge.live.conv.seat[expected]


def test_bridge_suggests_legal_moves_only_on_the_bots_turn():
    bridge = Bridge(random_net(hidden=(16, 16)), simulations=4)
    bridge.reset()
    moves, statuses = [], []
    for message in _messages("capture_full_game"):
        for reply in bridge.handle(message):
            if reply["kind"] == "status":
                statuses.append(reply)
            elif reply["kind"] == "move":
                moves.append(reply)
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


def test_bridge_server_never_closes_on_a_late_pong(monkeypatch):
    import websockets.asyncio.server as server

    seen = {}

    class FakeServe:
        def __init__(self, handler, host, port, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(server, "serve", FakeServe)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(serve_forever(lambda: None, port=18766))
    assert seen["ping_timeout"] is None


# ----- playing (--play) -----
def _recorded_log(name):
    from catanatron.colonist.convert import convert
    from catanatron.colonist.record import resolve_hidden_draws

    record = convert(load(os.path.join(FIXTURES, name + ".json"))).record
    return resolve_hidden_draws(record)


def _player_of_record(name):
    """A choose() that plays what was really played next, so every move the
    bridge sends is the one colonist then records."""
    from catanatron.colonist.replay import decision_of
    from catanatron.serialization import action_from_json

    log = _recorded_log(name)

    def choose(game):
        entry = log[len(game.state.action_records)]
        move = decision_of(action_from_json(entry[0]), game.playable_actions)
        return move, [(move, 1.0)], 0.0

    return choose


def _fake_encode(plan, context):
    return [json.dumps(plan).encode()]


def test_playing_sends_moves_and_confirms_each_one():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    sends = stops = 0
    for message in _messages("capture_full_game"):
        message = {**message, "socket": 3}
        for reply in bridge.handle(message):
            if reply["kind"] == "send":
                sends += 1
                assert reply["socket"] == 3
            if reply["kind"] == "status" and reply["state"] == "stopped":
                stops += 1
    assert stops == 0, bridge.live.error
    assert sends > 50


def test_playing_stops_when_colonist_records_another_move():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    real = _player_of_record("capture_full_game")

    def wrong(game):  # a legal move, but not the one that was played
        move, _, _ = real(game)
        other = next((a for a in game.playable_actions if a != move), move)
        return other, [(other, 1.0)], 0.0

    bridge.choose = wrong
    bridge.reset()
    statuses = []
    for message in _messages("capture_full_game"):
        statuses += [r for r in bridge.handle(message) if r["kind"] == "status"]
        if statuses and statuses[-1]["state"] == "stopped":
            break
    assert statuses[-1]["state"] == "stopped"
    assert "colonist recorded" in statuses[-1]["text"]


@pytest.mark.parametrize("timer", [False, True])
def test_discards_chosen_by_colonists_timer_are_followed(timer):
    """The bot can be too slow to discard (it waits for the earlier seat):
    colonist's timer then picks its cards (log 64, then 55 in the same update,
    seen live on bot4). That is no reason to stop; other cards still are."""
    import msgpack

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    real = _player_of_record("capture_full_game")

    def wrong_discard(game):
        move, _, _ = real(game)
        if move.action_type.value == "DISCARD_RESOURCE":
            other = next((a for a in game.playable_actions if a != move), move)
            return other, [(other, 1.0)], 0.0
        return move, [(move, 1.0)], 0.0

    bridge.choose = wrong_discard
    bridge.reset()
    statuses = []
    for message in _messages("capture_full_game"):
        doc = msgpack.unpackb(base64.b64decode(message["b64"]), strict_map_key=False)
        data = doc.get("data") if isinstance(doc, dict) else None
        if timer and isinstance(data, dict) and data.get("type") == C.DIFF:
            logs = (data.get("payload") or {}).get("diff", {}).get("gameLogState", {})
            you = bridge.live.conv.you if bridge.live else None
            if any(
                (e.get("text") or {}).get("type") == C.LOG_DISCARDED
                and e["text"].get("playerColor") == you
                for e in logs.values()
            ):
                logs["-1"] = {"text": {"type": 64, "playerColor": you}, "from": you}
                message = {
                    **message,
                    "b64": base64.b64encode(msgpack.packb(doc)).decode(),
                }
        statuses += [r for r in bridge.handle(message) if r["kind"] == "status"]
        if statuses and statuses[-1]["state"] == "stopped":
            break
    texts = [s["text"] for s in statuses]
    if timer:
        assert not any(s["state"] == "stopped" for s in statuses), texts
        assert any("timer discarded" in t for t in texts)
    else:
        assert statuses[-1]["state"] == "stopped"
        assert "colonist recorded discards" in statuses[-1]["text"]


def test_playing_stops_when_a_move_never_shows_up():
    import base64

    import msgpack

    bridge = Bridge(
        random_net(hidden=(8,)), 2, play=True, encode=_fake_encode, move_timeout=-1
    )
    bridge.reset()
    for message in _messages("capture_full_game"):
        if any(r["kind"] == "send" for r in bridge.handle(message)):
            break
    # Only a message that changes nothing arrives (a ping, say): the move is overdue.
    ping = base64.b64encode(msgpack.packb({"data": {"type": 999}})).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "b64": ping})
    assert replies[-1]["state"] == "stopped"
    assert "didn't show it" in replies[-1]["text"]


def test_replayed_frames_are_never_answered():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.reset()
    replies = []
    for message in _messages("capture_short_game"):
        replies += bridge.handle({**message, "replay": True})
    assert not [r for r in replies if r["kind"] in ("move", "send")]
    after = bridge.handle({"kind": "replay-done"})
    assert [r["kind"] for r in after] == (
        ["move", "send"] if bridge.live.bot_to_move else []
    )


def test_moves_without_a_known_format_are_only_suggested():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True)  # the real, empty client
    bridge.reset()
    replies = []
    for message in _messages("capture_short_game"):
        replies += bridge.handle(message)
    moves = [r for r in replies if r["kind"] == "move"]
    assert moves and not any(r["kind"] == "send" for r in replies)
    assert not any(m["played"] for m in moves)
    assert any("suggesting only" in r.get("text", "") for r in replies)


def test_playing_with_the_real_encoder_sends_game_messages_on_the_channel():
    import base64

    import msgpack

    from catanatron.colonist.capture import unpack_client

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True)  # the real client.encode
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    join = {
        "id": "1",
        "data": {
            "type": 1,
            "payload": {"serverId": "AB12CD", "reconnectToken": "secret"},
        },
    }
    bridge.handle(
        {
            "kind": "frame",
            "dir": "in",
            "b64": base64.b64encode(msgpack.packb(join)).decode(),
        }
    )
    sent, stops = [], 0
    for message in _messages("capture_full_game"):
        for reply in bridge.handle(message):
            if reply["kind"] == "send":
                sent.append(unpack_client(base64.b64decode(reply["b64"])))
            elif reply["kind"] == "status" and reply["state"] == "stopped":
                stops += 1
    assert stops == 0  # every move confirmed, discards included
    assert len(sent) > 50
    assert all(s["header"] == [3, 1] and s["channel"] == "AB12CD" for s in sent)
    assert {s["body"]["action"] for s in sent} >= {2, 6, 11, 15}
    assert "secret" not in json.dumps(bridge.__dict__, default=str)
    assert 7 in {s["body"]["action"] for s in sent}  # discards are sent


def test_resent_full_game_messages_are_not_new_games():
    """colonist resends the whole game (type 4) during play. Following the
    game from such a mid-game snapshot would lose the moves before it."""
    import base64

    import msgpack

    messages = _messages("capture_full_game")
    start = next(
        m
        for m in messages
        if msgpack.unpackb(base64.b64decode(m["b64"]), strict_map_key=False)
        .get("data", {})
        .get("type")
        == 4
    )
    odd = base64.b64encode(
        msgpack.packb({"data": {"type": 4, "payload": [1, 2]}})
    ).decode()
    bridge = Bridge(random_net(hidden=(8,)), 2)
    bridge.reset()
    statuses = []
    for i, message in enumerate(messages):
        if i in (60, 120):  # the full game again, mid-play, and another layout
            statuses += [r for r in bridge.handle(start) if r["kind"] == "status"]
            statuses += [
                r
                for r in bridge.handle({**message, "b64": odd})
                if r["kind"] == "status"
            ]
        statuses += [r for r in bridge.handle(message) if r["kind"] == "status"]
    assert [s["state"] for s in statuses] == ["following", "waiting"]
    assert bridge.live.error is None


def _follow(frames):
    start = next(f for f in frames if f.dir == "in" and f.type == C.GAME_STATE)
    live = LiveGame(start.data["data"]["payload"])
    for f in frames:
        if f.seq > start.seq:
            live.apply(f)
            assert live.error is None, live.error
            if live.conv.pending_robber is not None:
                assert not live.bot_to_move
    return live


def _robber_diffs(frames, steals):
    for f in frames:
        if f.type != C.DIFF:
            continue
        log = f.data["data"]["payload"].get("diff", {}).get("gameLogState", {})
        keys = sorted(log, key=int)
        kinds = [log[k].get("text", {}).get("type") for k in keys]
        if C.LOG_MOVED_ROBBER in kinds and kinds[-1] in steals:
            yield f, log, keys[-1]


def test_steal_logged_in_a_later_update():
    """Live, colonist can send the robber move and its steal separately."""
    import copy

    frames = load(os.path.join(FIXTURES, "capture_full_game.json"))
    expected = _follow(frames).record["log"]
    split, n = [], 0
    for f in frames:
        split.append(f)
        found = next(_robber_diffs([f], C.STEALS), None)
        if found is None:
            continue
        f, log, last = found
        steal = copy.deepcopy(f)
        del log[last]
        steal.data["data"]["payload"]["diff"] = {
            "gameLogState": {
                last: steal.data["data"]["payload"]["diff"]["gameLogState"][last]
            }
        }
        split.append(steal)
        n += 1
    assert n > 0
    assert _follow(split).record["log"] == expected


def test_robber_move_with_nothing_logged_robs_nobody():
    """With nobody to rob, colonist may log no "nothing to steal" entry."""
    frames = load(os.path.join(FIXTURES, "capture_full_game.json"))
    found = list(_robber_diffs(frames, (C.LOG_ROBBER_NOTHING_TO_STEAL,)))
    if not found:
        pytest.skip("no robber move without a steal in this capture")
    for _, log, last in found:
        del log[last]
    log = _follow(frames).record["log"]
    robs = [e for e in log if e[0][1] == "MOVE_ROBBER" and e[0][2][1] is None]
    assert len(robs) >= len(found)


def test_saves_each_game_as_a_capture_without_the_reconnect_token(tmp_path):
    import base64

    import msgpack

    from catanatron.colonist.convert import convert

    join = {"data": {"type": 1, "payload": {"serverId": "AB", "reconnectToken": "s3"}}}
    bridge = Bridge(random_net(hidden=(8,)), 2, save_dir=str(tmp_path))
    bridge.reset()
    bridge.handle(
        {
            "kind": "frame",
            "dir": "in",
            "b64": base64.b64encode(msgpack.packb(join)).decode(),
        }
    )
    for message in _messages("capture_full_game"):
        bridge.handle(message)
    path = bridge.save()
    assert path and path.startswith(str(tmp_path))
    frames = load(path)
    assert "s3" not in json.dumps([f.data for f in frames], default=str)
    assert any(f.type == 1 for f in frames)
    saved = convert(load(path)).record["log"]
    original = convert(load(os.path.join(FIXTURES, "capture_full_game.json")))
    assert saved == original.record["log"]
    assert bridge.channel == "AB"


def test_capture_rewrite_keeps_previous_json_readable_until_complete(
    tmp_path, monkeypatch
):
    bridge = Bridge.__new__(Bridge)
    bridge.save_dir = str(tmp_path)
    bridge.live = object()
    bridge.frames = [{"seq": 0, "t": 1, "dir": "open", "socket": 0, "text": ""}]
    bridge.saved_path = None
    path = bridge.save()
    original = json.load(open(path))

    real_dump = json.dump

    def interrupted_dump(doc, stream):
        stream.write('{"format":')
        assert json.load(open(path)) == original
        stream.seek(0)
        stream.truncate()
        real_dump(doc, stream)

    monkeypatch.setattr(json, "dump", interrupted_dump)
    bridge.frames.append({"seq": 1, "t": 2, "dir": "close", "socket": 0})
    assert bridge.save() == path
    assert len(json.load(open(path))["messages"]) == 2
    assert not list(tmp_path.glob(".bridge-*.json"))


def test_capture_records_which_net_played(tmp_path):
    bridge = Bridge.__new__(Bridge)
    bridge.save_dir = str(tmp_path)
    bridge.live = object()
    bridge.frames = [{"seq": 0, "t": 1, "dir": "open", "socket": 0, "text": ""}]
    bridge.saved_path = None
    bridge.bot = {"kind": "info", "net": "model.npz", "sha256": "ab" * 8}
    bridge.bot["label"] = "sp1 round 8"
    doc = json.load(open(bridge.save()))
    assert doc["bot"] == {
        "net": "model.npz",
        "sha256": "ab" * 8,
        "label": "sp1 round 8",
    }


def test_timeout_names_what_colonist_sent_instead():
    import base64

    import msgpack

    bridge = Bridge(
        random_net(hidden=(8,)), 2, play=True, encode=_fake_encode, move_timeout=-1
    )
    bridge.reset()
    for message in _messages("capture_full_game"):
        if any(r["kind"] == "send" for r in bridge.handle(message)):
            break
    ping = base64.b64encode(msgpack.packb({"data": {"type": 999}})).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "b64": ping})
    assert "message types: 999" in replies[-1]["text"]


def test_reconnect_retries_unconfirmed_end_turn_up_to_the_limit():
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    start = None
    for message in _messages("capture_full_game"):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
        bridge.handle({**message, "socket": 1})
        if bridge.expect and bridge.expect[1][0]["type"] == "END_TURN":
            break
    assert start is not None and bridge.expect is not None

    snapshot = copy.deepcopy(start)
    cur = snapshot["gameState"]["currentState"]
    cur["completedTurns"] = bridge.live.conv.completed
    cur["currentTurnPlayerColor"] = bridge.live.conv.current
    snapshot["gameState"]["gameLogState"] = {}
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    reconnect = {"kind": "frame", "dir": "in", "socket": 2, "b64": b64}
    assert not [
        r for r in bridge.handle({**reconnect, "replay": True}) if r["kind"] == "send"
    ]
    replies = bridge.handle(reconnect)
    assert [r["socket"] for r in replies if r["kind"] == "send"] == [2]
    assert bridge.expect is not None and bridge.retry_count == 1
    # A reconnect storm (a new account's session): each snapshot that shows
    # the move didn't land gets one more resend, up to the limit.
    for socket in range(3, 2 + MAX_RECONNECT_RETRIES):
        replies = bridge.handle({**reconnect, "socket": socket})
        assert [r["socket"] for r in replies if r["kind"] == "send"] == [socket]
    assert bridge.retry_count == MAX_RECONNECT_RETRIES
    replies = bridge.handle({**reconnect, "socket": 99})
    assert not [r for r in replies if r["kind"] == "send"]
    assert any(r["kind"] == "status" and r["state"] == "stopped" for r in replies)


def test_same_game_snapshot_stops_if_player_becomes_spectator():
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2)
    bridge.reset()
    start = next(
        message
        for message in _messages("capture_full_game")
        if decode({**message, "seq": 0, "socket": 1}).type == C.GAME_STATE
    )
    bridge.handle({**start, "socket": 1})
    snapshot = copy.deepcopy(
        decode({**start, "seq": 0, "socket": 1}).data["data"]["payload"]
    )
    snapshot["playerColor"] = 0
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "socket": 2, "b64": b64})
    assert any(
        r.get("state") == "stopped" and "spectator" in r["text"] for r in replies
    )


def test_robber_placement_waits_for_delayed_steal_log():
    bridge = Bridge(None, play=True, move_timeout=15)
    bridge.live = SimpleNamespace(
        conv=SimpleNamespace(you=1, pending_robber=(1, [0, 0, 0])),
        tile_of_cube={(0, 0, 0): 6},
        error=None,
    )
    plan = [{"type": "MOVE_ROBBER", "tile": 6, "label": "robber to tile 6"}]
    bridge.expect = (0, plan, time.time() - 16)
    assert bridge.check_timeout() == []
    assert bridge.live.error is None
    bridge.expect = (0, plan, time.time() - 76)
    assert bridge.check_timeout()[-1]["state"] == "stopped"


def test_acknowledged_road_without_board_effect_retries_only_once():
    bridge = Bridge(None, play=True, move_timeout=15)
    bridge.live = SimpleNamespace(
        bot_to_move=True,
        position_key=42,
        error=None,
    )
    bridge.socket = 1
    bridge.expect = (
        42,
        [{"type": "BUILD_ROAD", "edge": 12, "label": "road on edge 12"}],
        time.time() - 8,
    )
    bridge.sent_raws = [b"road request"]

    replies = bridge.check_timeout()
    assert [r["kind"] for r in replies] == ["status", "send"]
    assert bridge.no_effect_retry
    bridge.handle({"kind": "send-confirmed", "sendId": replies[1]["sendId"]})
    assert bridge.check_timeout() == []

    bridge.expect = (42, bridge.expect[1], time.time() - 16)
    assert bridge.check_timeout()[-1]["state"] == "stopped"


def test_page_reports_failed_send_immediately():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    assert bridge.expect is not None
    send_id = next(r["sendId"] for r in replies if r["kind"] == "send")
    replies = bridge.handle(
        {
            "kind": "send-failed",
            "socket": 1,
            "sendId": send_id,
            "reason": "game socket send failed",
        }
    )
    assert bridge.expect is None
    assert replies[-1]["state"] == "stopped"
    assert "game socket send failed" in replies[-1]["text"]


def test_closed_socket_waits_for_reconnect_and_resends_on_new_socket():
    # Ranked, 2026-10-12: the game socket closed just as a move was sent and
    # colonist rejoined the game on a new socket, as the same player, 1.3 s
    # later. Stopping at the failed send lost the game.
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    start = None
    for message in _messages("capture_full_game"):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect and bridge.expect[1][0]["type"] == "END_TURN":
            break
    assert start is not None and bridge.expect is not None
    send_id = next(r["sendId"] for r in replies if r["kind"] == "send")
    replies = bridge.handle(
        {
            "kind": "send-failed",
            "socket": 1,
            "sendId": send_id,
            "reason": "game socket not open",
        }
    )
    assert bridge.expect is not None and not bridge.live.error
    assert replies[-1]["state"] == "following"
    assert bridge.check_timeout() == []

    snapshot = copy.deepcopy(start)
    cur = snapshot["gameState"]["currentState"]
    cur["completedTurns"] = bridge.live.conv.completed
    cur["currentTurnPlayerColor"] = bridge.live.conv.current
    snapshot["gameState"]["gameLogState"] = {}
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "socket": 2, "b64": b64})
    assert [r["socket"] for r in replies if r["kind"] == "send"] == [2]
    assert bridge.expect is not None and not bridge.live.error


def test_closed_socket_without_reconnect_still_times_out():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    send_id = next(r["sendId"] for r in replies if r["kind"] == "send")
    bridge.handle(
        {
            "kind": "send-failed",
            "socket": 1,
            "sendId": send_id,
            "reason": "game socket not open",
        }
    )
    bridge.expect = (*bridge.expect[:2], time.time() - bridge.move_timeout - 1)
    assert bridge.check_timeout()[-1]["state"] == "stopped"
    assert bridge.expect is None


def test_unacknowledged_browser_send_retries_once_with_same_id():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    sent = next(r for r in replies if r["kind"] == "send")
    bridge.pending_sends[sent["sendId"]][1] -= ACK_SECONDS + 1
    retries = bridge.check_timeout()
    assert retries == [sent]
    assert bridge.pending_sends[sent["sendId"]][2] == 1
    bridge.handle({"kind": "send-confirmed", "sendId": sent["sendId"]})
    assert not bridge.pending_sends
    assert not bridge.check_timeout()


def test_a_browser_frozen_for_seven_seconds_does_not_stop_the_bot():
    # bot8's first game (Oct 19): on a VM short of memory the page froze 7 s
    # after the opening settlement was sent, and the 2 s window stopped the bot.
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    sent = next(r for r in replies if r["kind"] == "send")
    bridge.pending_sends[sent["sendId"]][1] -= 3
    assert bridge.check_timeout() == []
    bridge.pending_sends[sent["sendId"]][1] -= 4
    assert bridge.check_timeout() == [sent]
    assert bridge.expect is not None
    bridge.handle({"kind": "send-confirmed", "sendId": sent["sendId"]})
    assert not bridge.pending_sends


def test_a_move_confirmed_eleven_seconds_after_its_resend_keeps_the_game():
    # bot14 (Oct 29, 1:23 PM): resent at 32 s with the page on the lobby screen,
    # stopped at 37 s; the browser sent the move at 43 s and colonist took it.
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    sent = next(r for r in replies if r["kind"] == "send")
    bridge.pending_sends[sent["sendId"]][1] -= ACK_SECONDS + 1
    assert bridge.check_timeout() == [sent]
    bridge.pending_sends[sent["sendId"]][1] -= 11
    assert bridge.check_timeout() == []
    assert bridge.live.error is None
    bridge.handle({"kind": "send-confirmed", "sendId": sent["sendId"]})
    assert not bridge.pending_sends
    assert bridge.expect is not None


def test_a_move_never_confirmed_after_its_resend_stops_the_bot():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    sent = next(r for r in replies if r["kind"] == "send")
    bridge.pending_sends[sent["sendId"]][1] -= ACK_SECONDS + 1
    assert bridge.check_timeout() == [sent]
    bridge.pending_sends[sent["sendId"]][1] -= ACK_GIVE_UP_SECONDS + 1
    assert bridge.check_timeout()[-1]["state"] == "stopped"
    assert bridge.live.error == "browser did not acknowledge a move after retry"


def test_outgoing_frame_confirms_send_when_old_page_has_no_ack():
    from catanatron.colonist.capture import pack_client

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    for message in _messages("capture_full_game"):
        replies = bridge.handle({**message, "socket": 1})
        if bridge.expect:
            break
    bridge.send_replies(
        [pack_client((3, 1), "game", {"action": 3, "payload": 14, "sequence": 0})]
    )
    actual = pack_client((3, 1), "game", {"action": 3, "payload": 14, "sequence": 85})
    bridge.handle(
        {
            "kind": "frame",
            "dir": "out",
            "socket": 1,
            "b64": base64.b64encode(actual).decode(),
        }
    )
    assert not bridge.pending_sends
    assert not bridge.check_timeout()


def _join(bridge):
    import base64

    import msgpack

    join = {"id": "1", "data": {"type": 1, "payload": {"serverId": "AB12CD"}}}
    bridge.handle(
        {
            "kind": "frame",
            "dir": "in",
            "b64": base64.b64encode(msgpack.packb(join)).decode(),
        }
    )


def _sent_actions(replies):
    import base64

    from catanatron.colonist.capture import unpack_client

    return [
        unpack_client(base64.b64decode(r["b64"]))["body"]
        for r in replies
        if r["kind"] == "send"
    ]


def test_declines_trade_offers_once():
    """The bot never trades with players: an offer waiting on its answer is
    declined at once with colonist's message (50, response 1), and only once.
    The fixture has a real offer from the opponent to the recording seat."""
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True)
    bridge.choose = _player_of_record("capture_road_building")
    bridge.reset()
    _join(bridge)
    sent = []
    for message in _messages("capture_road_building"):
        sent += _sent_actions(bridge.handle(message))
    declines = [b for b in sent if b["action"] == 50]
    assert declines == [
        {"action": 50, "payload": {"id": "nZRC", "response": 1}, "sequence": 0}
    ]


def test_replayed_offers_are_not_declined():
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True)
    bridge.choose = _player_of_record("capture_road_building")
    bridge.reset()
    _join(bridge)
    sent = []
    for message in _messages("capture_road_building"):
        sent += _sent_actions(bridge.handle({**message, "replay": True}))
    sent += _sent_actions(bridge.handle({"kind": "replay-done"}))
    assert not [b for b in sent if b["action"] == 50]  # closed long before the end


def test_a_game_joined_midway_is_not_played():
    """After a page reload colonist sends the game as it stands, without its
    history. The bridge must not treat that as a new game and play on it."""
    import base64
    import copy

    import msgpack

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True)
    bridge.reset()
    _join(bridge)
    replies = []
    for message in _messages("capture_full_game"):
        doc = msgpack.unpackb(
            base64.b64decode(message["b64"]), raw=False, strict_map_key=False
        )
        if isinstance(doc, dict) and doc.get("data", {}).get("type") == C.GAME_STATE:
            doc = copy.deepcopy(doc)
            doc["data"]["payload"]["gameState"]["currentState"]["completedTurns"] = 12
            message = {**message, "b64": base64.b64encode(msgpack.packb(doc)).decode()}
        replies += bridge.handle(message)
        if any(r["kind"] == "status" and r["state"] == "stopped" for r in replies):
            break
    assert not [r for r in replies if r["kind"] == "send"]
    stops = [
        r["text"] for r in replies if r["kind"] == "status" and r["state"] == "stopped"
    ]
    assert stops and "already in progress" in stops[0]


def test_info_about_the_net_comes_first(tmp_path):
    """The extension's panel shows which net is playing: the bridge sends it
    first on every connection."""
    import numpy as np
    from websockets.asyncio.client import connect

    from catanatron.colonist.bridge import describe_info, net_info

    path = tmp_path / "net.npz"
    random_net(hidden=(8,)).save(str(path))
    with np.load(path) as z:
        np.savez(path, **{**{k: z[k] for k in z.files}, "meta_epoch": np.asarray(7)})
    info = net_info(str(path), 800, True)
    assert len(info.pop("sha256")) == 16
    assert info == {
        "kind": "info",
        "net": str(path),
        "simulations": 800,
        "play": True,
        "epoch": 7,
    }
    labeled = net_info(str(path), 800, True, label="sp1 round 8")
    assert labeled["label"] == "sp1 round 8"
    assert "(sp1 round 8)" in describe_info(labeled)
    filtered = net_info(str(path), 800, True, judgment_filter=True)
    assert filtered["judgment_filter"] == "last_road_v1"
    assert "judgment road filter" in describe_info(filtered)
    robber = net_info(str(path), 800, True, robber_filter=True)
    assert robber["robber_filter"] == "own_pips_v1"
    assert "robber own-pips filter" in describe_info(robber)
    assert describe_info(info) == f"net {path}, epoch 7, 800 simulations, playing"
    assert "epoch" not in net_info(str(tmp_path / "missing.npz"), 50, False)

    async def run():
        server = asyncio.create_task(
            serve_forever(
                lambda: Bridge(random_net(hidden=(8,)), 2), port=18766, info=info
            )
        )
        await asyncio.sleep(0.3)
        async with connect("ws://127.0.0.1:18766") as ws:
            first = json.loads(await ws.recv())
            second = json.loads(await ws.recv())
        server.cancel()
        return first, second

    first, second = asyncio.run(run())
    assert first == info
    assert second["kind"] == "status" and second["state"] == "waiting"


def _reconnect_with_unconfirmed(kind, dice_thrown):
    """Follow the full game until the bot sends `kind`, then reconnect with a
    snapshot of the same turn (dice thrown or not). Returns the replies."""
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    start = None
    for message in _messages("capture_full_game"):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
        bridge.handle({**message, "socket": 1})
        if bridge.expect and bridge.expect[1][0]["type"] == kind:
            break
    assert start is not None and bridge.expect is not None
    snapshot = copy.deepcopy(start)
    cur = snapshot["gameState"]["currentState"]
    cur["completedTurns"] = bridge.live.conv.completed
    cur["currentTurnPlayerColor"] = bridge.live.conv.current
    snapshot["gameState"]["gameLogState"] = {}
    snapshot["gameState"]["diceState"] = {"diceThrown": dice_thrown}
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    return bridge.handle({"kind": "frame", "dir": "in", "socket": 2, "b64": b64})


def test_reconnect_retries_an_unconfirmed_roll_once():
    replies = _reconnect_with_unconfirmed("ROLL", dice_thrown=False)
    assert [r["socket"] for r in replies if r["kind"] == "send"] == [2]
    assert not any(r.get("state") == "stopped" for r in replies)


def test_reconnect_after_the_dice_were_thrown_does_not_roll_again():
    replies = _reconnect_with_unconfirmed("ROLL", dice_thrown=True)
    assert not [r for r in replies if r["kind"] == "send"]
    assert any(r.get("state") == "stopped" for r in replies)


def test_reconnect_after_the_move_landed_catches_up_and_plays_on():
    """The roll went through while the socket was down: the snapshot already
    has its log entries. The bridge catches up from it and carries on."""
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    start, messages = None, list(_messages("capture_full_game"))
    for i, message in enumerate(messages):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
        bridge.handle({**message, "socket": 1})
        if bridge.expect and bridge.expect[1][0]["type"] == "ROLL":
            break
    # The roll's own update, which the dropped socket never delivered.
    landed = next(
        decode({**m, "seq": 0, "socket": 1})
        for m in messages[i + 1 :]
        if decode({**m, "seq": 0, "socket": 1}).type == C.DIFF
        and decode({**m, "seq": 0, "socket": 1})
        .data["data"]["payload"]
        .get("diff", {})
        .get("gameLogState")
    )
    diff = landed.data["data"]["payload"]["diff"]
    snapshot = copy.deepcopy(start)
    state = snapshot["gameState"]
    state["gameLogState"] = {**state.get("gameLogState", {}), **diff["gameLogState"]}
    state["currentState"] = {
        **state["currentState"],
        "completedTurns": bridge.live.conv.completed,
        "currentTurnPlayerColor": bridge.live.conv.current,
        **diff.get("currentState", {}),
    }
    state["diceState"] = {"diceThrown": True}
    before = len(bridge.live.record["log"])
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "socket": 2, "b64": b64})
    assert not any(r.get("state") == "stopped" for r in replies), replies
    assert bridge.live.error is None
    assert len(bridge.live.record["log"]) > before  # the roll was caught up


def test_a_snapshot_on_the_same_socket_recovers_an_unconfirmed_move():
    """colonist reconnected the game session without a new socket and resent
    the whole game (seen live): the lost end turn is resent, not left to time
    out."""
    import base64

    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.choose = _player_of_record("capture_full_game")
    bridge.reset()
    start = None
    for message in _messages("capture_full_game"):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
        bridge.handle({**message, "socket": 1})
        if bridge.expect and bridge.expect[1][0]["type"] == "END_TURN":
            break
    snapshot = copy.deepcopy(start)
    cur = snapshot["gameState"]["currentState"]
    cur["completedTurns"] = bridge.live.conv.completed
    cur["currentTurnPlayerColor"] = bridge.live.conv.current
    snapshot["gameState"]["gameLogState"] = {}
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    replies = bridge.handle({"kind": "frame", "dir": "in", "socket": 1, "b64": b64})
    assert [r["socket"] for r in replies if r["kind"] == "send"] == [1]
    assert not any(r.get("state") == "stopped" for r in replies)


def _discard_race(deadline_in):
    """A bridge whose game has just had a 7: both players must discard, and
    the bot sits in the later seat, so the simulator wants the opponent's
    discard first. colonist's timer acts in `deadline_in` seconds."""
    from catanatron.game import Game
    from catanatron.models.enums import Action, ActionRecord, ActionType
    from catanatron.models.player import Color, RandomPlayer
    from catanatron.state_functions import player_freqdeck_add

    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=3)
    while game.state.is_initial_build_phase:
        game.play_tick()
    for color in game.state.colors:
        player_freqdeck_add(game.state, color, [2, 2, 2, 2, 2])
    roller = game.state.current_color()
    game.execute(
        Action(roller, ActionType.ROLL, None),
        validate_action=False,
        action_record=ActionRecord(Action(roller, ActionType.ROLL, (3, 4)), (3, 4)),
    )
    assert game.state.is_discarding
    opponent = game.state.current_color()
    bot = next(c for c in game.state.colors if c != opponent)

    from catanatron.colonist.live import RESOURCE_ID

    def describe(action):  # as LiveGame.describe: colonist's resource ids
        return {
            "type": action.action_type.value,
            "resource": RESOURCE_ID[action.value],
            "label": f"discard {action.value}",
        }

    live = SimpleNamespace(
        game=game,
        bot=bot,
        error=None,
        over=False,
        bot_to_move=False,
        position_key=10,
        conv=SimpleNamespace(completed=5, you=1),
        describe=describe,
    )
    bridge = Bridge(random_net(hidden=(8,)), 2, play=True, encode=_fake_encode)
    bridge.live = live
    bridge.deadline = time.time() + deadline_in
    return bridge


def _opponent_discards(bridge):
    """The opponent's discard arrives: the bot is to move."""
    live = bridge.live
    while live.game.state.current_color() != live.bot:
        live.game.execute(live.game.playable_actions[0], validate_action=False)
    live.bot_to_move = True
    live.position_key += 1


def test_discard_is_planned_before_the_opponents():
    bridge = _discard_race(deadline_in=20)
    replies = bridge.think()
    assert [r["kind"] for r in replies] == ["status"]  # planned, not sent
    assert "planned discard" in replies[0]["text"]
    plan = bridge.early_discard["plan"]
    state = bridge.live.game.state
    assert len(plan) == state.discard_counts[state.color_to_index[bridge.live.bot]]
    assert all(m["type"] == "DISCARD_RESOURCE" for m in plan)
    assert bridge.think() == []  # planned once per 7
    assert bridge.early_discard_due() == []  # plenty of time left


def test_planned_discard_is_sent_when_the_opponent_is_too_slow():
    bridge = _discard_race(deadline_in=20)
    bridge.think()
    bridge.deadline = time.time() + 2  # colonist's timer is about to act
    replies = bridge.early_discard_due()
    assert [r["kind"] for r in replies] == ["status", "send"]
    assert bridge.expect[1] == bridge.early_discard["plan"]
    assert bridge.early_discard_due() == []  # sent once
    _opponent_discards(bridge)
    assert bridge.think() == []  # and not again when the opponent discards


def test_a_sent_discard_is_not_planned_again_before_it_shows():
    """bot4's 2026-10-18 capture: the bot (the earlier seat) sent its six
    discards, then the opponent's discard was logged first. The bot was still
    to move in the simulator, searched again and sent a second plan, so its
    real discard didn't match what was expected."""
    bridge = _discard_race(deadline_in=60)
    _opponent_discards(bridge)
    replies = bridge.think()
    assert any(r["kind"] == "send" for r in replies)
    plan = bridge.expect[1]
    bridge.live.position_key += 1  # another update before the bot's discard
    assert bridge.think() == []
    assert bridge.expect[1] == plan


@pytest.mark.parametrize("left, searched", [(60, True), (4, False)])
def test_late_opponent_discard_uses_the_plan_only_without_time(left, searched):
    bridge = _discard_race(deadline_in=20)
    bridge.think()
    planned = bridge.early_discard["plan"]
    calls = []
    real = bridge.choose

    def choose(game):
        calls.append(1)
        return real(game)

    bridge.choose = choose
    bridge.deadline = time.time() + left
    _opponent_discards(bridge)
    replies = bridge.think()
    move = next(r for r in replies if r["kind"] == "move")
    assert any(r["kind"] == "send" for r in replies)
    assert bool(calls) == searched
    if not searched:
        assert move["plan"] == planned


def test_colonists_timer_is_tracked_with_its_extensions():
    bridge = Bridge(random_net(hidden=(8,)), 2)
    now_ms = time.time() * 1000
    # The turn's actions: 30 s from the roll, 4 s ago.
    bridge.note_deadline({"startTime": now_ms - 4000, "allocatedTime": 30}, now_ms)
    assert 25 < bridge.time_left() <= 26.1
    # A build adds 5 s to the allowance, without a new start time.
    bridge.note_deadline({"allocatedTime": 35}, now_ms)
    assert 30 < bridge.time_left() <= 31.1
    # A robber move starts a fresh 15 s.
    bridge.note_deadline({"startTime": now_ms, "allocatedTime": 15}, now_ms)
    assert 14 < bridge.time_left() <= 15.1
    bridge.reset()
    assert bridge.time_left() is None


def test_game_start_snapshot_sets_the_first_settlements_timer():
    # The first seat's first settlement comes before any diff: its 60 s is in
    # the game's first snapshot, and a previous game's deadline is dropped.
    import msgpack

    from catanatron.colonist.capture import decode

    bridge = Bridge(random_net(hidden=(8,)), 2)
    bridge.reset()
    bridge.deadline = time.time() - 100  # a previous game's expired timer
    message = next(
        m
        for m in _messages("capture_full_game")
        if decode({**m, "seq": 0, "socket": 1}).type == C.GAME_STATE
    )
    payload = decode({**message, "seq": 0, "socket": 1}).data["data"]["payload"]
    allocated = payload["gameState"]["currentState"]["allocatedTime"]
    payload["gameState"]["currentState"]["startTime"] = time.time() * 1000
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": payload}})
    ).decode()
    bridge.handle({"kind": "frame", "dir": "in", "socket": 1, "b64": b64})
    assert allocated - 2 < bridge.time_left() <= allocated + 0.1

    # The extension reconnecting as the game began: the page resends the start
    # 3 s after it came, and its timer still counts (from when it came).
    bridge.reset()
    came = time.time() * 1000 - 3000
    payload["gameState"]["currentState"]["startTime"] = came
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": payload}})
    ).decode()
    bridge.handle(
        {
            "kind": "frame",
            "dir": "in",
            "socket": 1,
            "b64": b64,
            "t": came,
            "replay": True,
        }
    )
    assert allocated - 5 < bridge.time_left() <= allocated - 2.9


def _position(prompt):
    from catanatron.game import Game
    from catanatron.models.enums import ActionPrompt
    from catanatron.models.player import Color, RandomPlayer

    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=5)
    if prompt != "BUILD_INITIAL_SETTLEMENT":
        while game.state.is_initial_build_phase:
            game.play_tick()
    if prompt == "AFTER_ROLL":
        game.play_tick()  # the roll
        while game.state.current_prompt != ActionPrompt.PLAY_TURN:
            game.play_tick()  # a 7's discards and robber, if any
    return game


def test_each_decision_gets_its_share_of_the_time():
    from catanatron.colonist.bridge import think_budget

    setup = _position("BUILD_INITIAL_SETTLEMENT")
    assert think_budget(setup, 57.5, 2400) == (20.0, 20000)  # 40% of 55 s, capped
    assert think_budget(setup, 12.5, 2400) == (4.0, 20000)  # 40% of what's left
    roll = _position("ROLL")
    assert think_budget(roll, 3.0, 2400) == (0.165, 800)  # 3 s to roll
    turn = _position("AFTER_ROLL")
    seconds, sims = think_budget(turn, 30.0, 2400)
    assert (seconds, sims) == (5.0, 4800)
    assert think_budget(turn, 6.0, 2400)[0] == 0.3 * 3.5  # late in the turn
    assert think_budget(turn, 1.0, 2400)[0] == 0.05  # the floor
    assert think_budget(turn, None, 2400) == (None, 2400)  # no timer seen
    assert think_budget(setup, 57.5, 2)[1] == 17  # caps scale with --simulations


def test_free_roads_before_the_roll_get_the_build_budget():
    from catanatron.colonist.bridge import think_budget

    game = _position("ROLL")
    assert think_budget(game, 15.0, 2400) == (1.0, 800)  # the roll itself
    game.state.is_road_building = True  # Road Building played before rolling
    assert think_budget(game, 15.0, 2400) == (3.75, 4800)


def test_an_account_env_file_can_change_the_clock_shares(monkeypatch):
    from catanatron.colonist import bridge

    overrides = bridge.budgets_from_env("PLAY_TURN=0.3:10:5, ROLL=0.33:2:0.6667")
    assert overrides == {"PLAY_TURN": (0.3, 10.0, 5.0), "ROLL": (0.33, 2.0, 0.6667)}
    monkeypatch.setitem(bridge.BUDGETS, "PLAY_TURN", overrides["PLAY_TURN"])
    turn = _position("AFTER_ROLL")
    assert bridge.think_budget(turn, 30.0, 2400) == (0.3 * 27.5, 12000)
    assert bridge.budgets_from_env("") == {} and bridge.budgets_from_env(None) == {}
    for bad in ("TURN=0.3:10:5", "PLAY_TURN=0.3:10", "PLAY_TURN=2:10:5", "ROLL=x:1:1"):
        with pytest.raises(ValueError):
            bridge.budgets_from_env(bad)


def test_a_search_stops_when_its_time_is_up():
    from catanatron.colonist.mcts import Search

    game = _position("BUILD_INITIAL_SETTLEMENT")
    search = Search(random_net(hidden=(8,)), 100000, noise=False)
    start = time.monotonic()
    action, _ = search.run(game, time_limit=0.3)
    assert time.monotonic() - start < 3
    assert action in game.playable_actions and 0 < search.used < 100000
    search.run(game)  # no limit: the full budget
    assert search.used >= 99000


def test_a_search_with_no_choice_falls_back_to_the_nets_move(monkeypatch):
    """Redealt hidden VP cards can give the opponent 15 points, so the search
    finds no decision at its root; the bot plays the net's move instead."""
    from catanatron.colonist import mcts

    def no_choice(self, game, time_limit=None):
        raise ValueError("search needs a position with a choice to make")

    monkeypatch.setattr(mcts.Search, "run", no_choice)
    bridge = Bridge(random_net(hidden=(8,)), simulations=4)
    game = _position("BUILD_INITIAL_SETTLEMENT")
    move, shown, value = bridge.choose(game)
    assert (move, shown, value) == bridge.net_choice(game)
    assert move in game.playable_actions and len(shown) == 3


def test_a_discards_budget_is_shared_between_its_cards():
    bridge = _discard_race(deadline_in=20)
    seen = []
    real = bridge.choose

    def choose(game):
        seen.append(bridge.budget)
        return real(game)

    bridge.choose = choose
    bridge.budget = (4.0, 10)
    game = _bot_to_discard(bridge)
    count = game.state.discard_counts[game.state.color_to_index[bridge.live.bot]]
    plan = bridge.plan_discards(game)
    assert count > 1 and len(plan) == count
    assert all(b == (4.0 / count, 10) for b in seen)
    assert bridge.budget == (4.0, 10)  # restored


def _bot_to_discard(bridge):
    _opponent_discards(bridge)
    return bridge.live.game


def test_a_game_first_seen_mid_setup_is_followed_from_its_log():
    """The page first saw a game after the opening placements began (seen
    live: Sherie3424, during a reconnect loop). Its snapshot has the whole
    log, so the bridge starts from an empty board, catches up, and then
    follows the game exactly as a bridge that saw it from the start."""
    import base64

    import msgpack

    from catanatron.colonist.bridge import setup_start
    from catanatron.colonist.capture import decode

    messages = list(_messages("capture_full_game"))
    start, at = None, None
    snapshot = None
    for i, message in enumerate(messages):
        frame = decode({**message, "seq": 0, "socket": 1})
        if frame.dir != "in":
            continue
        if frame.type == C.GAME_STATE and isinstance(
            frame.data["data"].get("payload"), dict
        ):
            start = frame.data["data"]["payload"]
            snapshot = copy.deepcopy(start)
        elif frame.type == C.DIFF and snapshot is not None:
            diff = frame.data["data"]["payload"].get("diff", {})
            state = snapshot["gameState"]
            for part in ("tileCornerStates", "tileEdgeStates"):
                for k, v in (diff.get("mapState") or {}).get(part, {}).items():
                    state["mapState"][part].setdefault(k, {}).update(v)
            state["gameLogState"].update(diff.get("gameLogState") or {})
            state["currentState"].update(diff.get("currentState") or {})
            owned = [
                c
                for c in state["mapState"]["tileCornerStates"].values()
                if c.get("owner")
            ]
            if len(owned) == 2:  # one settlement each: mid-setup
                at = i
                break
    assert at is not None and setup_start(snapshot) is not None
    later = copy.deepcopy(snapshot)
    later["gameState"]["currentState"]["completedTurns"] = 99
    assert setup_start(later) is None  # past the opening: not recoverable

    seen_all = Bridge(random_net(hidden=(8,)), 2)
    seen_all.reset()
    for message in messages:
        seen_all.handle(message)
    b64 = base64.b64encode(
        msgpack.packb({"id": "1", "data": {"type": 4, "payload": snapshot}})
    ).decode()
    late = Bridge(random_net(hidden=(8,)), 2)
    late.reset()
    replies = late.handle({"kind": "frame", "dir": "in", "b64": b64, "t": 0})
    assert not any(r.get("state") == "stopped" for r in replies), replies
    for message in messages[at + 1 :]:
        late.handle(message)
    assert late.live.error is None
    assert late.live.record["log"] == seen_all.live.record["log"]


@pytest.mark.parametrize("wrong", [False, True])
def test_confirms_earlier_seats_discards_inserted_before_the_opponents(wrong):
    """bot3's 2026-10-14 capture: seven opponent discards arrived first,
    then the bot's eight were inserted before them by the converter."""
    from catanatron.models.player import Color
    from catanatron.colonist.live import RESOURCE_ID

    conv = C._Converter.__new__(C._Converter)
    conv.play_order = [1, 2]
    conv.seat = {1: "RED", 2: "BLUE"}
    conv.record = {"seats": ["RED", "BLUE"], "checks": []}
    conv.log = [[["RED", "ROLL", [3, 4]], [3, 4]]]
    conv.record["log"] = conv.log
    conv.discard_phase = None
    conv.discards = [(2, "WOOD")] * 7
    conv._flush_discards()
    before = len(conv.log)
    cards = ["SHEEP"] * 3 + ["ORE", "ORE", "SHEEP", "SHEEP", "WHEAT"]
    plan = [{"type": "DISCARD_RESOURCE", "resource": RESOURCE_ID[c]} for c in cards]
    if wrong:
        plan[0] = {"type": "DISCARD_RESOURCE", "resource": RESOURCE_ID["WOOD"]}
    bridge = Bridge(random_net(hidden=(8,)), 2)
    bridge.live = SimpleNamespace(
        conv=conv,
        record=conv.record,
        bot=Color.RED,
        error=None,
        position_key=before,
        describe=lambda a: {"resource": RESOURCE_ID[a.value]},
    )
    bridge.expect = (before, plan, time.time())
    conv.discards = [(1, c) for c in cards]
    conv._flush_discards()
    bridge.live.position_key = len(conv.log)
    replies = bridge.check_played(before)
    if wrong:
        assert replies[0]["state"] == "stopped"
        assert "colonist recorded discards" in bridge.live.error
    else:
        assert replies == []
        assert bridge.live.error is None
    assert bridge.expect is None


def test_a_pending_discard_waits_through_the_opponents():
    """bot5's 2026-10-16 capture: the bot (the earlier seat) sent its five
    discards, then the opponent's were logged first. That update wasn't the
    bot's move, so the sent plan must stay expected: forgetting it, the bot
    thought again, sent a second discard, and then stopped on its real one."""
    from catanatron.models.player import Color
    from catanatron.colonist.live import RESOURCE_ID

    conv = C._Converter.__new__(C._Converter)
    conv.play_order = [1, 2]
    conv.seat = {1: "RED", 2: "BLUE"}
    conv.record = {"seats": ["RED", "BLUE"], "checks": []}
    conv.log = [[["RED", "ROLL", [3, 4]], [3, 4]]]
    conv.record["log"] = conv.log
    conv.discard_phase = None
    before = len(conv.log)
    cards = ["ORE", "ORE", "WOOD", "ORE", "SHEEP"]
    plan = [{"type": "DISCARD_RESOURCE", "resource": RESOURCE_ID[c]} for c in cards]
    bridge = Bridge(random_net(hidden=(8,)), 2)
    bridge.live = SimpleNamespace(
        conv=conv,
        record=conv.record,
        bot=Color.RED,
        error=None,
        position_key=before,
        describe=lambda a: {"resource": RESOURCE_ID[a.value]},
    )
    bridge.expect = (before, plan, time.time())
    conv.discards = [(2, c) for c in ["ORE", "ORE", "BRICK", "WOOD", "WOOD"]]
    conv._flush_discards()
    bridge.live.position_key = len(conv.log)
    assert bridge.check_played(before) == []
    assert bridge.expect is not None and bridge.expect[1] == plan
    before = bridge.live.position_key
    conv.discards = [(1, c) for c in cards]
    conv._flush_discards()
    bridge.live.position_key = len(conv.log)
    assert bridge.check_played(before) == []
    assert bridge.live.error is None and bridge.expect is None
