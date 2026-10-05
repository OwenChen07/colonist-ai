"""The messages the bot sends: the same ones colonist's client sent.

fixtures/client_messages.json pairs every move the recording player made in a
real game (as LiveGame.describe gives it) with the game messages their client
sent for it (sequence numbers removed; interface-only messages dropped: 53
open the card menu, 66 hover a spot, 56/64 at the game's end). No names or ids.
"""

import json
import os

import msgpack
import pytest

from catanatron.colonist.capture import pack_client, unpack_client
from catanatron.colonist.client import NotKnownYet, bodies, encode

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "client_messages.json")


def _comparable(messages):
    """Resource choices (7, 8) compare by their final pick as a set (the
    order is the player's), and a doubled 'open the trade panel' (47) counts
    once."""
    out, last = [], None
    for m in messages:
        m = {k: v for k, v in m.items() if k != "sequence"}
        if m["action"] == 8 or (m["action"] == 47 and last == 47):
            continue
        if m["action"] == 7:
            m = {"action": 7, "payload": sorted(m["payload"])}
        out.append(m)
        last = m["action"]
    return out


def test_every_recorded_move_encodes_as_the_client_sent_it():
    with open(FIXTURE) as f:
        doc = json.load(f)
    kinds = set()
    for pair in doc["pairs"]:
        ours = []
        for move in pair["moves"]:
            kinds.add(move["type"])
            ours += bodies([move], {"creator": doc["creator"]})
        assert _comparable(ours) == _comparable(pair["sent"]), pair["moves"]
    assert {
        "ROLL",
        "END_TURN",
        "BUILD_SETTLEMENT",
        "BUILD_ROAD",
        "BUILD_CITY",
        "BUY_DEVELOPMENT_CARD",
        "MOVE_ROBBER",
        "MARITIME_TRADE",
        "PLAY_KNIGHT_CARD",
        "PLAY_MONOPOLY",
        "PLAY_YEAR_OF_PLENTY",
    } <= kinds


def test_client_message_layout_round_trips():
    raw = pack_client((3, 1), "07A428", {"action": 2, "payload": True, "sequence": 9})
    assert raw[:3] == bytes([3, 1, 6]) and raw[3:9] == b"07A428"
    assert unpack_client(raw) == {
        "header": [3, 1],
        "channel": "07A428",
        "body": {"action": 2, "payload": True, "sequence": 9},
    }
    assert unpack_client(b"\x01") is None


def test_encode_needs_the_channel_and_a_known_format():
    roll = [{"type": "ROLL", "label": "roll"}]
    with pytest.raises(NotKnownYet):
        encode(roll, {"creator": 2})
    [raw] = encode(roll, {"channel": "AB12", "creator": 2})
    assert unpack_client(raw)["body"]["action"] == 2
    with pytest.raises(NotKnownYet):
        encode(
            [{"type": "DISCARD_RESOURCE", "resource": 1, "label": "discard"}],
            {"channel": "AB12", "creator": 2},
        )
    # a city is the client's two messages, in order
    city = encode(
        [{"type": "BUILD_CITY", "corner": 37, "label": "city"}],
        {"channel": "AB12", "creator": 2},
    )
    assert [msgpack.unpackb(r[7:])["action"] for r in city] == [17, 18]
