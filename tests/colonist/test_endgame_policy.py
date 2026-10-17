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


def test_ze_spec_builds_the_filtered_player(tmp_path):
    from catanatron.colonist.endgame_policy import EndgameNetPlayer
    from catanatron.colonist.net import random_net
    import catanatron.players  # noqa: F401  (registers the bots)
    from catanatron.registry import REGISTRY

    net = tmp_path / "net.npz"
    random_net(hidden=(8,)).save(str(net))
    player = REGISTRY.build(f"ZE:{net}:2", Color.RED)
    assert isinstance(player, EndgameNetPlayer) and player.simulations == 2
    game = Game([player, RandomPlayer(Color.BLUE)], seed=4)
    game.play()


@pytest.mark.skipif(not os.path.exists(CAPTURE), reason="private capture not present")
def test_ship6230_yop_to_settlement_is_found():
    rows, won = scan(CAPTURE)
    assert not won
    missed = [r for r in rows if not r["followed"]]
    assert {r["turn"] for r in missed} == {74}
    assert all(r["path"] < r["legal"] for r in missed)
