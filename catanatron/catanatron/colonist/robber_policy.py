"""Root filter for the robber: never block your own income when a clean hex
blocks at least as much of the opponent's.

Seen live (E75 follow-up, Oct 21): at 2100+ the bot puts the robber on a hex
with its own pips while a hex with the opponent's pips and none of its own is
open on 15.5% of its robber moves, against 5.2% for people. In 1v1 every hex
touching the opponent steals the same, so such a move gives up income for
nothing. This filter narrows the root only; it changes no search descendants,
rules or weights.
"""

from catanatron.models.enums import ActionType, CITY


def hex_pips(state, coordinate):
    """{color: pips} of the buildings on a hex (cities doubled); empty for the
    desert."""
    tile = state.board.map.land_tiles[coordinate]
    if tile.number is None:
        return {}
    pips = 6 - abs(7 - tile.number)
    out = {}
    for node_id in tile.nodes.values():
        building = state.board.buildings.get(node_id)
        if building is not None:
            color, kind = building
            out[color] = out.get(color, 0) + pips * (2 if kind == CITY else 1)
    return out


def robber_root_actions(game, actions):
    """Drop robber moves onto your own pips that block no more of the
    opponent's pips than the best clean hex; every other move is kept."""
    if not actions or any(a.action_type != ActionType.MOVE_ROBBER for a in actions):
        return list(actions)
    state = game.state
    color = state.current_color()
    scores = {}
    for action in actions:
        coordinate = action.value[0]
        if coordinate not in scores:
            pips = hex_pips(state, coordinate)
            own = pips.get(color, 0)
            scores[coordinate] = (own, sum(pips.values()) - own)
    clean = [opp for own, opp in scores.values() if own == 0 and opp > 0]
    if not clean:
        return list(actions)
    best_clean = max(clean)
    keep = [
        a
        for a in actions
        if not (scores[a.value[0]][0] > 0 and scores[a.value[0]][1] <= best_clean)
    ]
    return keep or list(actions)
