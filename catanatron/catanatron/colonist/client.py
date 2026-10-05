"""Building the messages colonist's client sends for a move (PLAN.md, Phase 4).

colonist allows bots, and the bot plays by sending the same messages colonist's
own web client sends. This module turns a move, already in colonist's terms
(LiveGame.describe: corner/edge/tile indexes, resource ids), into those
messages as MessagePack bytes.

The message formats are not filled in yet: they come from what colonist's
client sent in recorded games (python -m catanatron.colonist.protocol
captures/*.json). Until a move type's format is known, encode() raises
NotKnownYet and the bridge only suggests that move.
"""

import msgpack


class NotKnownYet(NotImplementedError):
    """No message format is known for this move yet."""


# move type -> function(described move, game context) -> message (a Python
# object, packed with MessagePack). Filled in from protocol.py's summary.
FORMATS = {}


def encode(described_plan, context=None):
    """[MessagePack bytes] to send for a plan: one move, or a 7's discards
    (which colonist takes as one choice)."""
    kind = described_plan[0]["type"]
    build = FORMATS.get(kind)
    if build is None:
        raise NotKnownYet(
            f"no message format for {kind} yet "
            "(run python -m catanatron.colonist.protocol on captures that include one)"
        )
    messages = build(described_plan, context or {})
    return [msgpack.packb(m) for m in messages]
