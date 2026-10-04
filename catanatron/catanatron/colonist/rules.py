"""colonist.io's ranked 1v1 rules, as Catanatron options.

Sources: colonist's 1v1 guide (2023) and the colonist-tracker extension. The
number layout is not confirmed yet; colonist boards look random, so that is
the default until recorded games say otherwise (see PLAN.md, Phase 0).
"""

from typing import Optional, Sequence

from catanatron.game import Game
from catanatron.models.map import CatanMap
from catanatron.models.player import Player

COLONIST_1V1 = {
    "vps_to_win": 15,
    "discard_limit": 9,  # a 7 makes you discard only if you hold more than 9
    "friendly_robber": True,  # 2 or fewer visible points: can't be blocked or robbed
    "dice": "balanced",
}


def new_game(
    players: Sequence[Player],
    seed: Optional[int] = None,
    catan_map: Optional[CatanMap] = None,
    number_placement: str = "random",
    **overrides,
) -> Game:
    """A Game under colonist's 1v1 rules. Any rule can be overridden, e.g.
    dice="random" to train on plain dice."""
    rules = {**COLONIST_1V1, **overrides}
    return Game(
        players,
        seed=seed,
        catan_map=catan_map,
        number_placement=number_placement,
        **rules,
    )
