"""Isolated candidate that prunes narrowly wasteful last roads at the root.

This is an experiment, not the production bot. It changes no descendants in
search, no simulator rules, and no trained network weights.
"""

from catanatron.colonist import encode as E
from catanatron.colonist.bonus_tactics import (
    paid_road_facts,
    secure_against_remaining_road_and_settlement,
)
from catanatron.colonist.mcts import NetPlayer, Search
from catanatron.models.enums import ActionType
from catanatron.state_functions import player_key


def eligible_road_flags(state, actions):
    """Return action keys for roads with no demonstrated immediate gain."""
    if state.is_initial_build_phase or state.is_road_building or len(state.colors) != 2:
        return set()
    color = state.current_color()
    if (
        state.board.road_color != color
        or state.player_state[f"{player_key(state, color)}_ROADS_AVAILABLE"] != 1
        or not any(a.action_type == ActionType.BUILD_ROAD for a in actions)
    ):
        return set()
    secure = secure_against_remaining_road_and_settlement(state, color)
    if secure is not True:
        return set()
    flagged = set()
    for action in actions:
        if action.action_type == ActionType.BUILD_ROAD:
            facts = paid_road_facts(state, action, bonus_secure_before=secure)
            if facts and facts["no_immediate_tactical_gain"]:
                flagged.add(E.action_key(action))
    return flagged


def filtered_root_actions(state, actions):
    """Keep all moves when the narrow rule cannot safely remove one."""
    flagged = eligible_road_flags(state, actions)
    kept = [a for a in actions if E.action_key(a) not in flagged]
    return kept if kept else list(actions)


class LiveStyleNetPlayer(NetPlayer):
    """Evaluation baseline matching the bridge's deterministic root search."""

    LABEL = "Net+MCTS+LiveStyle"

    def root_actions(self, state, actions):
        return list(actions)

    def decide(self, game, playable_actions):
        if len(playable_actions) < 2:
            return playable_actions[0]
        if self.simulations < 1:
            raise ValueError("LiveStyleNetPlayer requires at least one simulation")
        kept = self.root_actions(game.state, playable_actions)
        keys = {E.action_key(a) for a in kept}
        root_filter = (
            (lambda g, actions: [a for a in actions if E.action_key(a) in keys])
            if len(kept) < len(playable_actions)
            else None
        )
        search = Search(
            self.net,
            self.simulations,
            rng=self.rng,
            noise=False,
            root_action_filter=root_filter,
        )
        action, _ = search.run(game)
        key = E.action_key(action)
        return next(a for a in playable_actions if E.action_key(a) == key)


class JudgmentPlayer(LiveStyleNetPlayer):
    """The live-style baseline with a root-only last-road filter."""

    LABEL = "Net+MCTS+Judgment"

    def root_actions(self, state, actions):
        return filtered_root_actions(state, actions)
