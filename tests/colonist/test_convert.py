"""Captures to records, on a real (scrubbed) colonist game.

fixtures/capture_short_game.json is a short casual 1v1: setup, three rolls,
then a resignation. Names and ids are replaced.
"""

import copy
import os

import pytest

from catanatron.colonist.capture import load
from catanatron.colonist.convert import ConvertError, convert, convert_map
from catanatron.colonist.replay import replay
from catanatron.serialization import map_from_json

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "capture_short_game.json")


@pytest.fixture
def frames():
    return load(FIXTURE)


def test_real_game_converts_and_replays(frames):
    conv = convert(frames)
    assert conv.ended == "resignation"
    assert not conv.unsupported
    record = conv.record
    assert record["rules"] == {
        "vps_to_win": 15,
        "discard_limit": 9,
        "friendly_robber": True,
        "dice": "balanced",
    }
    assert record["players"]["BLUE"]["you"] and not record["players"]["RED"]["you"]
    types = [entry[0][1] for entry in record["log"]]
    assert types[:8] == ["BUILD_SETTLEMENT", "BUILD_ROAD"] * 4
    assert types.count("ROLL") == 3

    report = replay(record)
    assert report.ok, report.summary()
    assert report.dice_mode == "balanced"
    assert len(record["checks"]) == 7  # all of them held


def test_board(frames):
    record = convert(frames).record
    catan_map = map_from_json(record["map"])
    resources = [t.resource for t in catan_map.land_tiles.values()]
    assert sorted(map(str, resources)) == sorted(
        map(
            str,
            ["WOOD"] * 4
            + ["BRICK"] * 3
            + ["SHEEP"] * 4
            + ["WHEAT"] * 4
            + ["ORE"] * 3
            + [None],
        )
    )
    ports = [t["resource"] for t in record["map"]["tiles"] if t["type"] == "PORT"]
    assert ports.count(None) == 4
    assert sorted(p for p in ports if p) == ["BRICK", "ORE", "SHEEP", "WHEAT", "WOOD"]
    desert = next(c for c, t in catan_map.land_tiles.items() if t.resource is None)
    assert tuple(record["robber"]) == desert


def test_starting_resources_come_from_the_right_tiles(frames):
    """The opening hands colonist dealt are what Catanatron deals from the
    mapped settlements, which pins down corners, tiles and resources."""
    record = convert(frames).record
    report = replay(record)
    assert report.ok, report.summary()
    first_hand = next(c for c in record["checks"] if "hand" in c)
    assert first_hand["hand"] == {
        "WOOD": 0,
        "BRICK": 0,
        "SHEEP": 2,
        "WHEAT": 0,
        "ORE": 1,
    }


def with_extra_log_entry(frames, text):
    frames = copy.deepcopy(frames)
    diff = next(f for f in frames if f.type == 91)
    diff.data["data"]["payload"]["diff"].setdefault("gameLogState", {})["999"] = {
        "text": text
    }
    return frames


def test_unknown_log_entry_stops_the_record(frames):
    conv = convert(with_extra_log_entry(frames, {"type": 9999, "playerColor": 1}))
    assert conv.ended == "unsupported"
    assert "9999" in conv.unsupported[0]
    assert replay(conv.record).ok  # what was converted is still sound


def test_capture_without_game_start_is_rejected(frames):
    with pytest.raises(ConvertError):
        convert([f for f in frames if f.type != 4])


def test_mismatched_ports_are_rejected(frames):
    start = next(f for f in frames if f.type == 4)
    map_state = copy.deepcopy(start.data["data"]["payload"]["gameState"]["mapState"])
    port = map_state["portEdgeStates"]["0"]
    port["x"], port["y"] = 0, 0  # an inland edge
    with pytest.raises(ConvertError):
        convert_map(map_state)
