"""Old vs new robber_possibilities on states from random-play games, for every
color and both robber modes; then the time per call of each.

usage: robber_equiv.py [games]
"""

import random
import sys
import timeit

from catanatron.colonist.selfplay import fresh_game
from catanatron.models import actions as A
from catanatron.models.enums import ActionType
from catanatron.models.enums import Action
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


games = int(sys.argv[1]) if len(sys.argv) > 1 else 40
states = []
for s in range(games):
    g = fresh_game(5000 + s)
    rng = random.Random(s)
    step = 0
    while g.winning_color() is None and step < 1500:
        g.execute(rng.choice(g.playable_actions))
        step += 1
        if step % 7 == 0:
            states.append(g.copy().state)
checked = differ = filtered_cases = 0
for st in states:
    for friendly in (False, True):
        st.friendly_robber = friendly
        for color in st.colors:
            a, b = old(st, color), A.robber_possibilities(st, color)
            checked += 1
            if a != b:
                differ += 1
            if friendly and len(a) < len(old_without(st, color)):
                filtered_cases += 1
print(
    f"{checked} checks on {len(states)} states, {differ} differ, "
    f"{filtered_cases} with friendly-robber filtering"
)
for st in states:
    st.friendly_robber = True
for name, fn in (("old", old), ("new", A.robber_possibilities)):
    t = min(
        timeit.repeat(
            lambda: [fn(st, st.colors[0]) for st in states], number=5, repeat=3
        )
    )
    print(f"{name}: {t / 5 / len(states) * 1e6:.2f} us per call")
