"""Building the messages colonist's client sends for a move (PLAN.md, Phase 4).

colonist allows bots, and the bot plays by sending the same messages colonist's
own web client sends. This module turns a move, already in colonist's terms
(LiveGame.describe: corner/edge/tile indexes, resource ids), into those bytes.

What the client sends (worked out from recorded games with protocol.py):

  - A game message is two header bytes (3, 1), the game's channel name (the
    room's serverId, from the message colonist sends when you join), then
    MessagePack {"action": code, "payload": ..., "sequence": n}. `sequence`
    counts up across every game message the client sends; the extension
    renumbers each outgoing one (bot/src/page.js), so the value here is a
    placeholder.
  - Codes seen, with their payloads:
        2  roll (true)                  6  end turn (true)
        9  buy development card (true)  3  move robber (tile index; in 1v1
                                           the steal is automatic)
        11 road (edge index)            15 settlement (corner index)
        17 then 18: city (true, then the corner index)
        48 play a development card (card code: 11 knight, 13 monopoly,
           14 road building, 15 year of plenty)
        8  choose resources for monopoly / year of plenty (the choice so far)
        7  confirm that choice
        47 then 49: bank or port trade (true, then the offer:
           {creator, isBankTrade, counterOfferInResponseToTradeId,
            offeredResources, wantedResources}, resources as colonist ids)
    The client also sends interface messages the server doesn't need for a
    move (53: open the card menu, 66: hover a spot); the bot doesn't.
  - Not seen yet: discards. Road building is assumed to be code 48 with card
    14 (colonist's code for that card, as the converter maps it), but no
    capture checked here includes one; the bridge confirms every move it
    sends, so a wrong guess stops the game rather than going unnoticed.

Until a move type's format is known, encode() raises NotKnownYet and the
bridge only suggests that move.
"""

from catanatron.colonist.capture import pack_client

GAME_HEADER = (3, 1)
CARD = {"knight": 11, "monopoly": 13, "road_building": 14, "year_of_plenty": 15}


class NotKnownYet(NotImplementedError):
    """No message format is known for this move yet."""


def _msg(action, payload):
    return {"action": action, "payload": payload, "sequence": 0}


def _resource_choice(card, resources):
    msgs = [_msg(48, CARD[card])]
    chosen = []
    for r in resources:
        chosen.append(r)
        msgs.append(_msg(8, list(chosen)))
    msgs.append(_msg(7, list(chosen)))
    return msgs


def _trade(move, context):
    return [
        _msg(47, True),
        _msg(
            49,
            {
                "creator": context["creator"],
                "isBankTrade": True,
                "counterOfferInResponseToTradeId": None,
                "offeredResources": [move["give"]] * move["give_count"],
                "wantedResources": [move["get"]],
            },
        ),
    ]


# move type -> function(described move, context) -> [message bodies]
FORMATS = {
    "ROLL": lambda m, c: [_msg(2, True)],
    "END_TURN": lambda m, c: [_msg(6, True)],
    "BUY_DEVELOPMENT_CARD": lambda m, c: [_msg(9, True)],
    "BUILD_ROAD": lambda m, c: [_msg(11, m["edge"])],
    "BUILD_SETTLEMENT": lambda m, c: [_msg(15, m["corner"])],
    "BUILD_CITY": lambda m, c: [_msg(17, True), _msg(18, m["corner"])],
    "MOVE_ROBBER": lambda m, c: [_msg(3, m["tile"])],
    "PLAY_KNIGHT_CARD": lambda m, c: [_msg(48, CARD["knight"])],
    "PLAY_ROAD_BUILDING": lambda m, c: [_msg(48, CARD["road_building"])],
    "PLAY_MONOPOLY": lambda m, c: _resource_choice("monopoly", [m["resource"]]),
    "PLAY_YEAR_OF_PLENTY": lambda m, c: _resource_choice(
        "year_of_plenty", m["resources"]
    ),
    "MARITIME_TRADE": _trade,
}


def bodies(described_plan, context):
    """The message bodies for a plan (one move, or a 7's discards)."""
    kind = described_plan[0]["type"]
    build = FORMATS.get(kind)
    if build is None:
        raise NotKnownYet(
            f"no message format for {kind} yet "
            "(run python -m catanatron.colonist.protocol on captures that include one)"
        )
    return build(described_plan[0], context)


def encode(described_plan, context):
    """[bytes to send] for a plan. context: {"channel": the game's serverId,
    "creator": the bot's colonist color}."""
    if not context.get("channel"):
        raise NotKnownYet(
            "the game's channel isn't known yet (no room-join message seen)"
        )
    return [
        pack_client(GAME_HEADER, context["channel"], b)
        for b in bodies(described_plan, context)
    ]
