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
    assert len(record["checks"]) == 9  # all of them held (cards and points)


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
    # What was converted is still sound; the record is just marked incomplete.
    assert replay(conv.record).applied == len(conv.record["log"])


def test_bank_shortage_notice_does_not_stop_conversion(frames):
    notice = {
        "type": 146,
        "cardNumber": 3,
        "count": 4,
        "bankCount": 3,
        "distributedCount": 0,
        "playerColors": [1, 5],
    }
    conv = convert(with_extra_log_entry(frames, notice))
    assert not conv.unsupported
    assert replay(conv.record).ok


def test_piece_notice_before_matching_setup_road_is_informational(frames):
    frames = copy.deepcopy(frames)
    diff = next(
        f
        for f in frames
        if f.type == 91
        and any(
            e.get("text", {}).get("type") == 4
            and e.get("text", {}).get("pieceEnum") == 0
            for e in f.data["data"]["payload"]["diff"].get("gameLogState", {}).values()
        )
    )
    logs = diff.data["data"]["payload"]["diff"]["gameLogState"]
    road = next(
        e["text"] for e in logs.values() if e.get("text", {}).get("pieceEnum") == 0
    )
    logs[str(min(map(int, logs)) - 1)] = {
        "text": {"type": 72, "pieceEnum": 0, "playerColor": road["playerColor"]}
    }
    conv = convert(frames)
    assert not conv.unsupported
    assert replay(conv.record).ok


def test_piece_notice_before_matching_setup_settlement_is_informational(frames):
    frames = copy.deepcopy(frames)
    diff = next(
        f
        for f in frames
        if f.type == 91
        and any(
            e.get("text", {}).get("type") == 4
            and e.get("text", {}).get("pieceEnum") == 2
            for e in f.data["data"]["payload"]["diff"].get("gameLogState", {}).values()
        )
    )
    logs = diff.data["data"]["payload"]["diff"]["gameLogState"]
    settlement = next(
        e["text"] for e in logs.values() if e.get("text", {}).get("pieceEnum") == 2
    )
    logs[str(min(map(int, logs)) - 1)] = {
        "text": {"type": 87, "pieceEnum": 2, "playerColor": settlement["playerColor"]}
    }
    conv = convert(frames)
    assert not conv.unsupported
    assert replay(conv.record).ok


def test_partially_distributed_bank_shortage_for_one_player_is_informational(frames):
    notice = {
        "type": 146,
        "cardEnum": 4,
        "count": 2,
        "bankCount": 1,
        "distributedCount": 1,
        "playerColors": [1],
    }
    conv = convert(with_extra_log_entry(frames, notice))
    assert not conv.unsupported
    assert replay(conv.record).ok


def test_invalid_partial_bank_distribution_stops(frames):
    notice = {
        "type": 146,
        "count": 2,
        "bankCount": 1,
        "distributedCount": 1,
        "playerColors": [1, 5],
    }
    conv = convert(with_extra_log_entry(frames, notice))
    assert conv.ended == "unsupported"
    assert "bank shortage with distribution" in conv.unsupported[0]


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


FULL_GAME = os.path.join(
    os.path.dirname(__file__), "fixtures", "capture_full_game.json"
)


@pytest.fixture(scope="module")
def full_game():
    return convert(load(FULL_GAME))


def test_full_game_replays_to_the_same_winner(full_game):
    """A real 20-minute game played to 15 points: 7s, discards, steals,
    knights, monopoly, year of plenty, hidden dev card buys, port trades."""
    assert full_game.ended == "win" and not full_game.unsupported
    record = full_game.record
    report = replay(record)
    assert report.ok, report.summary()
    assert report.winner == record["winner"]
    assert report.dice_mode == "balanced"  # 72 rolls, all possible under balanced dice
    kinds = {e[0][1] for e in record["log"]}
    assert {
        "MOVE_ROBBER",
        "DISCARD_RESOURCE",
        "PLAY_KNIGHT_CARD",
        "PLAY_MONOPOLY",
        "PLAY_YEAR_OF_PLENTY",
        "BUY_DEVELOPMENT_CARD",
        "MARITIME_TRADE",
        "BUILD_CITY",
    } <= kinds
    checks = record["checks"]
    assert sum("hand" in c for c in checks) > 50
    assert sum("visible_vps" in c for c in checks) > 10


def test_full_game_dev_cards_are_known_only_to_their_buyer(full_game):
    record = full_game.record
    you = next(c for c, p in record["players"].items() if p["you"])
    buys = [e for e in record["log"] if e[0][1] == "BUY_DEVELOPMENT_CARD"]
    assert all(e[1] is not None for e in buys if e[0][0] == you)
    assert all(e[1] is None for e in buys if e[0][0] != you)


def test_full_game_wrong_steal_is_caught(full_game):
    record = copy.deepcopy(full_game.record)
    i = next(
        i for i, e in enumerate(record["log"]) if e[0][1] == "MOVE_ROBBER" and e[1]
    )
    record["log"][i][1] = "WOOD" if record["log"][i][1] != "WOOD" else "BRICK"
    assert not replay(record).ok


def bank_trade(given, received, ratios):
    from catanatron.colonist.convert import _Converter

    c = _Converter.__new__(_Converter)
    c.log, c.discards, c.seat = [], [], {1: "RED"}
    c.pending_play = None
    c.ratios = {1: ratios}
    c._bank_trade(1, given, received)
    return [e[0][2] for e in c.log]


def test_bundled_bank_trade_is_split_into_lots():
    # 2:1 ore port plus 4:1 brick, received wheat and wool.
    assert bank_trade([5, 5, 2, 2, 2, 2], [4, 3], {5: 2, 2: 4}) == [
        ["ORE", "ORE", None, None, "WHEAT"],
        ["BRICK", "BRICK", "BRICK", "BRICK", "SHEEP"],
    ]
    assert bank_trade([3, 3, 3], [1], {3: 3}) == [
        ["SHEEP", "SHEEP", "SHEEP", None, "WOOD"]
    ]


ROAD_BUILDING = os.path.join(
    os.path.dirname(__file__), "fixtures", "capture_road_building.json"
)


def test_road_building_and_declined_trade_offer():
    """A short real game: the opponent offers a trade (declined, so nothing
    changes), and the recording player plays Road Building."""
    conv = convert(load(ROAD_BUILDING))
    assert not conv.unsupported
    kinds = [e[0][1] for e in conv.record["log"]]
    i = kinds.index("PLAY_ROAD_BUILDING")
    assert kinds[i + 1 : i + 3] == ["BUILD_ROAD", "BUILD_ROAD"]
    report = replay(conv.record)
    assert report.ok, report.summary()


def test_incomplete_conversion_fails_the_replay_check(frames):
    conv = convert(with_extra_log_entry(frames, {"type": 9999, "playerColor": 1}))
    assert conv.record["incomplete"]
    report = replay(conv.record)
    assert not report.ok
    assert "stopped early" in report.errors[0]


@pytest.mark.parametrize(
    "notice",
    [
        # "Bot is selecting where to place Robber for ...": the move follows as 11
        {"type": 58, "playerColor": 1, "pieceEnum": 5},
        # "Bot is selecting cards to discard for ...": the discard follows as 55
        {"type": 64, "playerColor": 1},
        # how many players the robber can steal from, logged with the move
        {"type": 139, "playerCount": 1},
        # a player disconnecting (with isConnected false in the same update)
        {"type": 140, "playerColor": 1},
    ],
)
def test_timer_playing_for_a_player_is_skipped(frames, notice):
    """When a player's timer runs out, colonist plays for them and logs a
    notice before the move itself (both seen live)."""
    conv = convert(with_extra_log_entry(frames, notice))
    assert not conv.unsupported


def test_monopoly_cards_in_a_later_update(full_game):
    """colonist's computer players choose a monopoly's resource in a separate
    step, so the card (20) and what it took (86) can come in different
    updates (seen live). The record must come out the same."""
    import dataclasses

    frames = load(FULL_GAME)
    i = next(
        i
        for i, f in enumerate(frames)
        if f.type == 91
        and any(
            e["text"].get("type") == 86
            for e in f.data["data"]["payload"]["diff"].get("gameLogState", {}).values()
        )
    )
    first = copy.deepcopy(frames[i])
    diff = first.data["data"]["payload"]["diff"]
    logs = diff["gameLogState"]
    later_logs = {k: v for k, v in logs.items() if v["text"].get("type") == 86}
    diff["gameLogState"] = {k: v for k, v in logs.items() if k not in later_logs}
    moved = {k: diff.pop(k) for k in ("playerStates", "bankState") if k in diff}
    second = copy.deepcopy(first)
    second.data["data"]["payload"]["diff"] = {"gameLogState": later_logs, **moved}
    second = dataclasses.replace(second, seq=frames[i].seq)
    split = frames[:i] + [first, second] + frames[i + 1 :]
    conv = convert(split)
    assert not conv.unsupported, conv.unsupported
    assert conv.record["log"] == full_game.record["log"]


def test_unfinished_opponent_monopoly_takes_nothing(frames):
    """colonist's Easy bot has played Monopoly and let its turn end without
    choosing (seen live). The card is spent and nothing moves: recorded as a
    monopoly on a resource the recording player holds none of."""
    conv = convert(
        with_extra_log_entry(frames, {"type": 20, "playerColor": 1, "cardEnum": 13})
    )
    assert not conv.unsupported, conv.unsupported
    plays = [e for e in conv.record["log"] if e[0][1] == "PLAY_MONOPOLY"]
    assert len(plays) == 1 and plays[0][0][0] == "RED"


def test_unfinished_year_of_plenty_stops_the_record(frames):
    conv = convert(
        with_extra_log_entry(frames, {"type": 20, "playerColor": 1, "cardEnum": 15})
    )
    assert conv.unsupported and "never came" in conv.unsupported[0]


def test_a_player_who_leaves_forfeits(frames):
    """When the opponent leaves, colonist logs the other player as the winner,
    short of 15 points, with no resignation entry: that is a forfeit."""
    from dataclasses import replace

    from catanatron.colonist.convert import DIFF, GAME_OVER

    frames = copy.deepcopy(frames)
    last = None
    for f in frames:
        if f.dir == "in" and f.type == DIFF:
            logs = f.data["data"]["payload"].get("diff", {}).get("gameLogState") or {}
            for key in [k for k, v in logs.items() if v["text"]["type"] == 112]:
                del logs[key]  # no resignation entry
            last = f
    end = {
        "players": {
            "1": {"color": 1, "victoryPoints": {"0": 2, "1": 1}, "winningPlayer": True},
            "2": {"color": 2, "victoryPoints": {"0": 2}, "winningPlayer": False},
        }
    }
    game_over = replace(
        last,
        seq=last.seq + 1,
        data={"data": {"type": GAME_OVER, "payload": {"endGameState": end}}},
    )
    conv = convert(frames + [game_over])
    assert conv.ended == "resignation" and not conv.unsupported
    record = conv.record
    assert record["forfeit"] and "winner" not in record
    assert record["resigned"] == "BLUE"  # the one who left; RED was logged the winner
    assert replay(record).ok


def test_a_win_on_points_is_not_a_forfeit(frames):
    from dataclasses import replace

    from catanatron.colonist.convert import GAME_OVER

    conv = convert(frames)
    assert "forfeit" not in conv.record
    last = frames[-1]
    # 5 settlements, 4 cities and longest road: 15 points (from the full game)
    end = {
        "players": {
            "1": {"victoryPoints": {"0": 5, "1": 4, "4": 1}},
            "2": {"victoryPoints": {}},
        }
    }
    game_over = replace(
        last,
        seq=last.seq + 1,
        dir="in",
        data={"data": {"type": GAME_OVER, "payload": {"endGameState": end}}},
    )
    assert "forfeit" not in convert(frames + [game_over]).record


def test_an_earlier_seats_late_discards_go_first():
    """colonist's timer can discard for each player in separate updates, the
    later seat first (seen live when the bot's bridge had stopped). Catanatron
    asks the first seat first, so its discards go before, and the checks move
    with them."""
    from catanatron.colonist.convert import _Converter

    c = _Converter.__new__(_Converter)
    c.play_order = [2, 1]  # colonist colors: 2 sits first (RED), 1 second (BLUE)
    c.seat = {2: "RED", 1: "BLUE"}
    c.record = {"seats": ["RED", "BLUE"], "checks": []}
    c.log = [[["BLUE", "ROLL", [6, 1]], [6, 1]]]
    c.discard_phase = None
    c.discards = [(1, "ORE"), (1, "WOOD")]
    c._flush_discards()  # BLUE's discards arrive first
    c.record["checks"] += [
        {"at": 3, "color": "BLUE", "resources": 11},  # after its discards
        {"at": 3, "color": "RED", "resources": 12},  # RED hasn't discarded yet
    ]
    c.discards = [(2, "SHEEP")]
    c._flush_discards()
    assert [e[0][:2] for e in c.log[1:]] == [["RED", "DISCARD_RESOURCE"]] + [
        ["BLUE", "DISCARD_RESOURCE"]
    ] * 2
    assert c.record["checks"] == [
        {"at": 4, "color": "BLUE", "resources": 11},
        {"at": 1, "color": "RED", "resources": 12},
    ]
