"""Root filter for the endgame: when a +1 VP build is one YoP or trade away,
play toward it.

Seen live (ship6230): at 13/15 VP with Year of Plenty in hand and a new
settlement site, the search split visits among 15 YoP combos, a 4:1 trade and
END_TURN, and often picked a combo that builds nothing. This filter narrows
the root only; it changes no search descendants, rules or weights.
"""

from catanatron.colonist import encode as E
from catanatron.colonist.bonus_audit import immediate_winning_actions
from catanatron.colonist.mcts import NetPlayer, Search
from catanatron.models.enums import ActionType
from catanatron.state_functions import player_key

BUILD_TYPES = (ActionType.BUILD_SETTLEMENT, ActionType.BUILD_CITY)
#: Deterministic resource moves that can fund a build.
FUNDING_TYPES = (ActionType.PLAY_YEAR_OF_PLENTY, ActionType.MARITIME_TRADE)


def _has_build(game, color):
    return any(
        a.color == color and a.action_type in BUILD_TYPES for a in game.playable_actions
    )


def _reaches_build(game, color, depth):
    """True if some chain of at most `depth` funding moves unlocks a build."""
    for action in game.playable_actions:
        if action.color != color or action.action_type not in FUNDING_TYPES:
            continue
        after = game.copy()
        after.execute(action, validate_action=False)
        if after.state.current_color() != color:
            continue
        if _has_build(after, color) or (
            depth > 1 and _reaches_build(after, color, depth - 1)
        ):
            return True
    return False


def build_path_actions(game, actions, margin=3, depth=2):
    """Root actions on a path to a settlement/city this turn, or [] when the
    rule does not apply (far from the target, a build is already legal, the
    game is not a plain 1v1 main-phase turn, or no path exists)."""
    state = game.state
    if state.is_initial_build_phase or state.is_road_building or len(state.colors) != 2:
        return []
    color = state.current_color()
    points = state.player_state[player_key(state, color) + "_ACTUAL_VICTORY_POINTS"]
    if points < game.vps_to_win - margin or points >= game.vps_to_win:
        return []
    if _has_build(game, color):
        return []
    path = []
    for action in actions:
        if action.action_type not in FUNDING_TYPES:
            continue
        after = game.copy()
        after.execute(action, validate_action=False)
        if after.state.current_color() == color and (
            _has_build(after, color)
            or (depth > 1 and _reaches_build(after, color, depth - 1))
        ):
            path.append(action)
    return path


def endgame_root_actions(game, actions, margin=3, depth=2):
    """Keep only actions toward a build (plus any immediate win) when one is
    reachable via funding moves; otherwise keep every move."""
    path = build_path_actions(game, actions, margin, depth)
    if not path:
        return list(actions)
    keep = {E.action_key(a) for a in path}
    color = game.state.current_color()
    keep |= {E.action_key(a) for a in immediate_winning_actions(game, color)}
    return [a for a in actions if E.action_key(a) in keep]


class EndgameNetPlayer(NetPlayer):
    """ZE:<net.npz>:<simulations>: the Z player with `endgame_root_actions`
    as its root filter, so a match against Z isolates the filter."""

    LABEL = "Net+MCTS+Endgame"

    def decide(self, game, playable_actions):
        if len(playable_actions) == 1 or self.simulations == 0:
            return super().decide(game, playable_actions)
        search = Search(
            self.net,
            self.simulations,
            rng=self.rng,
            root_action_filter=endgame_root_actions,
        )
        action, _ = search.run(game)
        key = E.action_key(action)
        return next(a for a in playable_actions if E.action_key(a) == key)
