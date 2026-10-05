"""Pairing a capture's outgoing messages with the moves they caused."""

import base64
import json
import os

import msgpack

from catanatron.colonist.protocol import scrub, summarize

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "capture_full_game.json")


def test_pairs_outgoing_messages_with_moves(tmp_path):
    with open(FIXTURE) as f:
        doc = json.load(f)
    # The fixture keeps only incoming messages. Put a fake outgoing message
    # before every update, plus a chat line that causes nothing.
    messages, seq = [], 0
    for m in doc["messages"]:
        for out in ({"action": 99, "payload": {"n": seq}}, {"chat": {"text": "hi"}}):
            raw = msgpack.packb(out)
            messages.append(
                {
                    "seq": seq,
                    "t": m["t"],
                    "dir": "out",
                    "socket": m["socket"],
                    "b64": base64.b64encode(raw).decode(),
                }
            )
            seq += 1
        messages.append({**m, "seq": seq})
        seq += 1
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({**doc, "messages": messages}))

    summary = summarize([str(path)])
    moves = summary["moves"]
    assert "ROLL" in moves and "BUILD_ROAD" in moves
    roll = moves["ROLL"]
    # the move message's shape appears in every instance; so does the chat
    # line here, which is why both are reported with their counts
    top = {
        json.dumps(s["shape"], sort_keys=True): s["in"] for s in roll["message_shapes"]
    }
    assert (
        top[json.dumps({"action": "int", "payload": {"n": "int"}}, sort_keys=True)]
        == roll["seen"]
    )
    example = roll["examples"][0]
    assert example["caused"][0][1] == "ROLL"
    assert {"chat": {"text": "<scrubbed>"}} in example["sent"]


def test_scrub_removes_personal_details():
    out = scrub(
        {
            "username": "Owen",
            "data": {"text": "gg", "x": 3, "token": "abc"},
            "id": "x" * 40,
        }
    )
    assert out == {
        "username": "<scrubbed>",
        "data": {"text": "<scrubbed>", "x": 3, "token": "<scrubbed>"},
        "id": "<long string>",
    }
