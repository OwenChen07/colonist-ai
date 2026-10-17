import os

import pytest

from catanatron.colonist import encode as E
from catanatron.colonist.endgame_policy import build_path_actions, endgame_root_actions
from catanatron.colonist.endgame_set import scan
from catanatron.game import Game
from catanatron.models.player import Color, RandomPlayer

CAPTURE = "captures/bot3/bridge-20261017-023003.json"


def test_far_from_target_keeps_every_move():
    game = Game([RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)], seed=3)
    actions = list(game.playable_actions)
    assert build_path_actions(game, actions) == []
    kept = endgame_root_actions(game, actions)
    assert [E.action_key(a) for a in kept] == [E.action_key(a) for a in actions]


@pytest.mark.skipif(not os.path.exists(CAPTURE), reason="private capture not present")
def test_ship6230_yop_to_settlement_is_found():
    rows, won = scan(CAPTURE)
    assert not won
    missed = [r for r in rows if not r["followed"]]
    assert {r["turn"] for r in missed} == {74}
    assert all(r["path"] < r["legal"] for r in missed)
