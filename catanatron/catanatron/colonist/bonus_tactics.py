"""Public, exact bonus-race facts for offline decision audits.

These checks deliberately make narrow claims. A road with no immediate
tactical gain may still have long-term value, so none of the flags here is a
training label or a live-play prohibition by itself.
"""

from dataclasses import dataclass

from catanatron.models.enums import ActionType
from catanatron.state_functions import (
    get_largest_army,
    get_longest_road_length,
    get_played_dev_cards,
    player_key,
    player_num_resource_cards,
)

_UNSET = object()


@dataclass(frozen=True)
class ArmyRace:
    """Played knights needed to claim or take Largest Army in a two-seat game."""

    holder: object
    mine: int
    theirs: int
    mine_to_claim: int
    theirs_to_claim: int


def army_race(state, color):
    """A tie keeps the incumbent; an unclaimed army first requires three."""
    if len(state.colors) != 2:
        raise ValueError("army_race requires a two-seat game")
    other = next(c for c in state.colors if c != color)
    holder, _ = get_largest_army(state)
    mine = get_played_dev_cards(state, color, "KNIGHT")
    theirs = get_played_dev_cards(state, other, "KNIGHT")
    mine_target = max(3, theirs + (1 if holder == other else 0))
    theirs_target = max(3, mine + (1 if holder == color else 0))
    mine_to_claim = 0 if holder == color else max(0, mine_target - mine)
    theirs_to_claim = 0 if holder == other else max(0, theirs_target - theirs)
    return ArmyRace(holder, mine, theirs, mine_to_claim, theirs_to_claim)


def _opponent_still_cannot_take(board, color, other, settlements_left):
    """Check this board and every currently reachable opponent settlement."""
    if board.road_color != color:
        return False
    if not settlements_left:
        return True
    for node in board.buildable_node_ids(other):
        after = board.copy()
        after.build_settlement(other, node, False)
        if after.road_color != color:
            return False
    return True


def secure_against_remaining_road_and_settlement(state, color):
    """Whether the holder survives every use of at most one remaining piece each.

    Returns None if the opponent has more than one road or settlement piece;
    that larger search is deliberately left unknown. This checks road then
    settlement and settlement then road, without assuming the opponent can pay.
    Cities, trades and future card draws are outside this short horizon.
    """
    if len(state.colors) != 2 or state.board.road_color != color:
        return None
    other = next(c for c in state.colors if c != color)
    key = player_key(state, other)
    roads = state.player_state[f"{key}_ROADS_AVAILABLE"]
    settlements = state.player_state[f"{key}_SETTLEMENTS_AVAILABLE"]
    if roads > 1 or settlements > 1:
        return None
    board = state.board
    if not _opponent_still_cannot_take(board, color, other, settlements):
        return False
    if roads:
        for edge in board.buildable_edges(other):
            after_road = board.copy()
            after_road.build_road(other, edge)
            if not _opponent_still_cannot_take(after_road, color, other, settlements):
                return False
    if roads and settlements:
        for node in board.buildable_node_ids(other):
            after_settlement = board.copy()
            after_settlement.build_settlement(other, node, False)
            for edge in after_settlement.buildable_edges(other):
                after_both = after_settlement.copy()
                after_both.build_road(other, edge)
                if after_both.road_color != color:
                    return False
    return True


def paid_road_facts(state, action, bonus_secure_before=_UNSET):
    """Describe one paid road; flag a *narrow* lack of immediate tactical gain.

    A flagged road uses the last piece, cannot avert a bonus loss within the
    opponent's remaining road/settlement pieces, opens no settlement site,
    blocks no currently buildable opponent edge, and is not needed to reduce
    the builder's hand below the discard limit. It is not called a losing move.
    """
    if (
        action.action_type != ActionType.BUILD_ROAD
        or state.is_initial_build_phase
        or state.is_road_building
        or len(state.colors) != 2
    ):
        return None
    color = action.color
    other = next(c for c in state.colors if c != color)
    board = state.board
    key = player_key(state, color)
    roads_left = state.player_state[f"{key}_ROADS_AVAILABLE"]
    sites_before = set(board.buildable_node_ids(color))
    blocks_current_edge = tuple(sorted(action.value)) in {
        tuple(sorted(edge)) for edge in board.buildable_edges(other)
    }
    if bonus_secure_before is _UNSET:
        bonus_secure_before = secure_against_remaining_road_and_settlement(state, color)
    after = board.copy()
    after.build_road(color, action.value)
    sites_added = len(set(after.buildable_node_ids(color)) - sites_before)
    hand_within_limit = player_num_resource_cards(state, color) <= state.discard_limit
    return {
        "last_piece": roads_left == 1,
        "longest_road_gain": after.road_lengths[color]
        - get_longest_road_length(state, color),
        "sites_added": sites_added,
        "blocks_current_opponent_edge": blocks_current_edge,
        "bonus_secure_before": bonus_secure_before,
        "hand_within_discard_limit": hand_within_limit,
        "no_immediate_tactical_gain": bool(
            roads_left == 1
            and bonus_secure_before is True
            and after.road_color == color
            and sites_added == 0
            and not blocks_current_edge
            and hand_within_limit
        ),
    }
