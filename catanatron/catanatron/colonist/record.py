"""Game records: one finished (or unfinished) game, as a JSON document.

The recorder writes these from colonist; the simulator can write them too,
which is how the format is tested. Everything is already in Catanatron's terms
(node ids, edges, cube coordinates, colors), so a replay is a straight walk
through the log. Translating colonist's own coordinates is the recorder's job.

    {
      "format": "colonist-ai.game-record",
      "version": 1,
      "rules": {"vps_to_win": 15, "discard_limit": 9,
                "friendly_robber": true, "dice": "balanced"},
      "map": <serialization.map_to_json>,
      "robber": [x, y, z],              # optional; defaults to the desert
      "seats": ["RED", "BLUE"],         # turn order
      "players": {"RED": {"name": "...", "you": true}, ...},   # optional
      "log": [[action, result], ...],   # serialization.action_record_to_json
      "checks": [                       # optional
        {"at": 12, "color": "RED", "resources": 4, "dev_cards": 1,
         "visible_vps": 3, "hand": {"WOOD": 1, ...}}   # each field optional
      ],
      "hidden_vp_cards": {"BLUE": 1},   # optional; revealed at game end
      "winner": "BLUE",                 # optional
      "source": {...}                   # optional, free-form
    }

"at" is the number of log entries applied when the check holds.

A result is null where the recording player could not see it: the
opponent's development card draws. resolve_hidden_draws() fills those in from
the cards the opponent later plays, which is all a replay needs.
"""

import json
from collections import Counter

from catanatron.models.decks import starting_devcard_bank
from catanatron.models.enums import (
    KNIGHT,
    MONOPOLY,
    RESOURCES,
    ROAD_BUILDING,
    VICTORY_POINT,
    YEAR_OF_PLENTY,
    ActionType,
)
from catanatron.serialization import action_record_to_json, map_to_json
from catanatron.state_functions import (
    get_visible_victory_points,
    player_key,
    player_num_dev_cards,
    player_num_resource_cards,
)

FORMAT = "colonist-ai.game-record"
VERSION = 1

PLAYED_CARD = {
    ActionType.PLAY_KNIGHT_CARD.value: KNIGHT,
    ActionType.PLAY_YEAR_OF_PLENTY.value: YEAR_OF_PLENTY,
    ActionType.PLAY_MONOPOLY.value: MONOPOLY,
    ActionType.PLAY_ROAD_BUILDING.value: ROAD_BUILDING,
}
BUY = ActionType.BUY_DEVELOPMENT_CARD.value
END_TURN = ActionType.END_TURN.value


class RecordError(ValueError):
    pass


def load(path):
    with open(path) as f:
        record = json.load(f)
    check_format(record)
    return record


def save(record, path):
    with open(path, "w") as f:
        json.dump(record, f, indent=1)


def check_format(record):
    if record.get("format") != FORMAT:
        raise RecordError(f"not a game record (format={record.get('format')!r})")
    if record.get("version") != VERSION:
        raise RecordError(f"unsupported record version {record.get('version')!r}")
    for key in ("rules", "map", "seats", "log"):
        if key not in record:
            raise RecordError(f"record is missing {key!r}")


# ----- writing records from the simulator -----
def record_from_game(game, perspective=None, checks=True):
    """A record of a simulated game. With `perspective` (a Color), the other
    players' development card draws are hidden, the way colonist shows them."""
    s = game.state
    log = []
    for r in s.action_records:
        action, result = action_record_to_json(r)
        if (
            perspective is not None
            and action[1] == BUY
            and action[0] != perspective.value
        ):
            action, result = [action[0], BUY, None], None
        log.append([action, result])
    record = {
        "format": FORMAT,
        "version": VERSION,
        "rules": {
            "vps_to_win": game.vps_to_win,
            "discard_limit": s.discard_limit,
            "friendly_robber": s.friendly_robber,
            "dice": "balanced" if s.dice is not None else "random",
        },
        "map": map_to_json(s.board.map),
        "seats": [c.value for c in s.colors],
        "log": log,
        "source": {"simulator": True, "seed": game.seed},
    }
    if perspective is not None:
        record["hidden_vp_cards"] = {
            c.value: s.player_state[f"{player_key(s, c)}_VICTORY_POINT_IN_HAND"]
            for c in s.colors
            if c != perspective
        }
    if game.winning_color() is not None:
        record["winner"] = game.winning_color().value
    if checks:
        record["checks"] = [snapshot(s, c, len(log)) for c in s.colors]
    return record


def snapshot(state, color, at):
    """A check that holds for `color` after `at` log entries."""
    key = player_key(state, color)
    return {
        "at": at,
        "color": color.value,
        "resources": player_num_resource_cards(state, color),
        "dev_cards": player_num_dev_cards(state, color),
        "visible_vps": get_visible_victory_points(state, color),
        "hand": {r: state.player_state[f"{key}_{r}_IN_HAND"] for r in RESOURCES},
    }


# ----- hidden development card draws -----
def resolve_hidden_draws(record):
    """Return a copy of the log with every hidden draw (result null) filled in.

    A card played on some turn must have been bought on an earlier turn, so
    each play of a card the player isn't known to hold is matched to their
    oldest hidden draw from an earlier turn. Draws never played become victory
    point cards, as many as `hidden_vp_cards` says, and otherwise whichever
    non-VP card the deck has most of left (an unplayed card only matters to a
    replay if it is worth points).
    """
    log = [list(entry) for entry in record["log"]]
    turn = 0
    known = (
        {}
    )  # color -> Counter of known, unplayed cards: card -> list of turns bought
    hidden = {}  # color -> list of (log index, turn bought) not yet matched
    assigned = Counter()  # every card whose identity is known or assigned
    for i, (action, result) in enumerate(log):
        color, kind = action[0], action[1]
        if kind == END_TURN:
            turn += 1
        elif kind == BUY:
            if result is None:
                hidden.setdefault(color, []).append((i, turn))
            else:
                known.setdefault(color, Counter())[(result, turn)] += 1
                assigned[result] += 1
        elif kind in PLAYED_CARD:
            card = PLAYED_CARD[kind]
            mine = known.get(color, Counter())
            held = sorted(
                t for (c, t), n in mine.items() if c == card and t < turn and n
            )
            if held:
                mine[(card, held[0])] -= 1
                continue
            options = [h for h in hidden.get(color, []) if h[1] < turn]
            if not options:
                raise RecordError(
                    f"log entry {i}: {color} plays {card} without having bought one "
                    f"on an earlier turn"
                )
            j, _ = options[0]
            hidden[color].remove(options[0])
            log[j] = [[color, BUY, card], card]
            assigned[card] += 1

    left = Counter(starting_devcard_bank()) - assigned
    vp_cards = dict(record.get("hidden_vp_cards", {}))
    for color, draws in hidden.items():
        for j, _ in draws:
            if vp_cards.get(color, 0) > 0 and left[VICTORY_POINT] > 0:
                card = VICTORY_POINT
                vp_cards[color] -= 1
            else:
                non_vp = [c for c in left if c != VICTORY_POINT and left[c] > 0]
                if not non_vp:
                    card = VICTORY_POINT
                else:
                    card = max(non_vp, key=lambda c: left[c])
            left[card] -= 1
            log[j] = [[color, BUY, card], card]
    return log
