"""The previous robber_possibilities, for ab_shard.py's 'old' arm."""

from catanatron.models.enums import Action, ActionType
from catanatron.state_functions import (
    get_visible_victory_points,
    player_num_resource_cards,
)


def old_without(state, color):
    actions = []
    for coordinate, tile in state.board.map.land_tiles.items():
        if coordinate == state.board.robber_coordinate:
            continue
        to_steal_from = set()
        for node_id in tile.nodes.values():
            building = state.board.buildings.get(node_id, None)
            if building is not None:
                candidate_color = building[0]
                if (
                    player_num_resource_cards(state, candidate_color) >= 1
                    and color != candidate_color
                ):
                    to_steal_from.add(candidate_color)
        if len(to_steal_from) == 0:
            actions.append(Action(color, ActionType.MOVE_ROBBER, (coordinate, None)))
        else:
            for enemy_color in to_steal_from:
                actions.append(
                    Action(color, ActionType.MOVE_ROBBER, (coordinate, enemy_color))
                )
    return actions


def old_blocks(state, action):
    coordinate, _ = action.value
    tile = state.board.map.land_tiles[coordinate]
    for node_id in tile.nodes.values():
        building = state.board.buildings.get(node_id, None)
        if building is None:
            continue
        if get_visible_victory_points(state, building[0]) < 3:
            return True
    return False


def old(state, color):
    actions = old_without(state, color)
    if not state.friendly_robber:
        return actions
    filtered = [a for a in actions if not old_blocks(state, a)]
    return filtered if filtered else actions
