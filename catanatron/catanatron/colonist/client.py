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
           In setup each is sent alone. In the main game the client first
           sends 10 (true) before a road and 14 (true) before a settlement;
           colonist ignores a bare 11 or 15 there (seen live). The free
           roads of Road Building are 11 then 21, both with the edge.
        17 then 18: city (true, then the corner index)
        48 play a development card (card code: 11 knight, 13 monopoly,
           14 road building, 15 year of plenty)
        8  choose resources for monopoly / year of plenty, or the cards to
           discard on a 7 (the choice so far, sent after each pick)
        7  confirm that choice. A discard is just 8s then 7, with no card
           played first (from a discard made by hand in a live game).
        47 then 49: bank or port trade (true, then the offer:
           {creator, isBankTrade, counterOfferInResponseToTradeId,
            offeredResources, wantedResources}, resources as colonist ids)
        50 answer a trade offer from another player ({"id": offer id,
           "response": 1} declines; colonist then shows the bot's response
           as 2 and closes the offer). The bot never trades with players,
           so declining is the only answer it sends.
    The client also sends interface messages the server doesn't need for a
    move (53: open the card menu, 66: hover a spot); the bot doesn't.
  - Road Building (48 with card 14, then 11 + 21 per
    free road) is taken from one recorded game but not yet played live; the
    bridge confirms every move it sends, so a wrong format stops the game
    rather than going unnoticed.

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


def _discard(plan):
    """A 7's discards: the whole plan at once, as one choice."""
    msgs, chosen = [], []
    for move in plan:
        chosen.append(move["resource"])
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


def _phase(context):
    """ "setup", "road_building" (Road Building's free roads) or "main"."""
    phase = context.get("phase")
    if phase not in ("setup", "road_building", "main"):
        raise ValueError(f"building needs the game phase in the context, got {phase!r}")
    return phase


def _road(move, context):
    edge = move["edge"]
    return {
        "setup": [_msg(11, edge)],
        "road_building": [_msg(11, edge), _msg(21, edge)],
        "main": [_msg(10, True), _msg(11, edge)],
    }[_phase(context)]


def _settlement(move, context):
    if _phase(context) == "setup":
        return [_msg(15, move["corner"])]
    return [_msg(14, True), _msg(15, move["corner"])]


# move type -> function(described move, context) -> [message bodies]
FORMATS = {
    "ROLL": lambda m, c: [_msg(2, True)],
    "END_TURN": lambda m, c: [_msg(6, True)],
    "BUY_DEVELOPMENT_CARD": lambda m, c: [_msg(9, True)],
    "BUILD_ROAD": _road,
    "BUILD_SETTLEMENT": _settlement,
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
    if kind == "DISCARD_RESOURCE":
        return _discard(described_plan)
    build = FORMATS.get(kind)
    if build is None:
        raise NotKnownYet(
            f"no message format for {kind} yet "
            "(run python -m catanatron.colonist.protocol on captures that include one)"
        )
    return build(described_plan[0], context)


def encode(described_plan, context):
    """[bytes to send] for a plan. context: {"channel": the game's serverId,
    "creator": the bot's colonist color, "phase": "setup" | "road_building" |
    "main" (needed for roads and settlements)}."""
    if not context.get("channel"):
        raise NotKnownYet(
            "the game's channel isn't known yet (no room-join message seen)"
        )
    return [
        pack_client(GAME_HEADER, context["channel"], b)
        for b in bodies(described_plan, context)
    ]


def decline_trade(offer_id, context):
    """[bytes to send] to decline another player's trade offer."""
    if not context.get("channel"):
        raise NotKnownYet(
            "the game's channel isn't known yet (no room-join message seen)"
        )
    body = _msg(50, {"id": offer_id, "response": 1})
    return [pack_client(GAME_HEADER, context["channel"], body)]
